"""PTC Sandbox - Manages local code execution using ipybox for Programmatic Tool Calling.

This module provides a sandbox environment that runs Python code locally using
ipybox's IPython kernel, eliminating the need for remote Daytona API calls.
"""

import asyncio
import hashlib
import json
import os
import shlex
import subprocess
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

import aiofiles
import structlog

from ipybox import CodeExecutor, CodeExecutionResult, CodeExecutionError

from ptc_agent.config.core import CoreConfig

from .mcp_registry import MCPRegistry
from .tool_generator import ToolFunctionGenerator

logger = structlog.get_logger(__name__)


@dataclass
class ChartData:
    """Captured chart from matplotlib execution."""

    type: str
    title: str
    png_base64: str | None = None
    elements: list[Any] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """Result of code execution in sandbox."""

    success: bool
    stdout: str
    stderr: str
    duration: float
    files_created: list[str]
    files_modified: list[str]
    execution_id: str
    code_hash: str
    charts: list[ChartData] = field(default_factory=list)


class PTCSandbox:
    """Manages local code execution using ipybox for Programmatic Tool Calling (PTC).

    This sandbox runs Python code locally using ipybox's IPython kernel,
    providing a stateful execution environment without remote API dependencies.
    """

    # Default Python dependencies available in the environment
    DEFAULT_DEPENDENCIES = [
        # Core
        "mcp", "fastmcp", "pandas", "requests", "aiohttp", "httpx",
        # Data science
        "numpy", "scipy", "scikit-learn", "statsmodels",
        # Financial data
        "yfinance",
        # Visualization
        "matplotlib", "seaborn", "plotly", "mplfinance==0.12.10b0",
        # Image analysis
        "pillow", "opencv-python-headless", "scikit-image",
        # File formats
        "openpyxl", "xlrd", "python-docx", "pypdf",
        "beautifulsoup4", "lxml", "pyyaml",
        # Utilities
        "tqdm", "tabulate",
    ]

    def __init__(self, config: CoreConfig, mcp_registry: MCPRegistry | None = None) -> None:
        """Initialize PTC sandbox.

        Args:
            config: Configuration object
            mcp_registry: MCP registry with connected servers (can be None for reconnect)
        """
        self.config = config
        self.mcp_registry = mcp_registry

        # ipybox code executor
        self._code_executor: CodeExecutor | None = None
        self.sandbox_id: str | None = None
        self.tool_generator = ToolFunctionGenerator()
        self.execution_count = 0
        self.bash_execution_count = 0

        # Working directory for local file operations
        self._work_dir = Path(config.filesystem.working_directory)
        self._tools_dir = self._work_dir / "tools"
        self._results_dir = self._work_dir / "results"
        self._data_dir = self._work_dir / "data"
        self._code_dir = self._work_dir / "code"

        logger.info("Initialized PTCSandbox with ipybox backend", work_dir=str(self._work_dir))

    def _get_mcp_packages(self) -> list[str]:
        """Extract MCP package names from enabled stdio servers.

        Returns:
            List of MCP package names to install globally
        """
        mcp_packages = []
        for server in self.config.mcp.servers:
            if not server.enabled:
                continue
            if server.transport == "stdio" and server.command == "npx":
                # Extract package name from npx arguments
                # Format: ["npx", "-y", "package-name", ...]
                if len(server.args) >= 2 and server.args[0] == "-y":
                    mcp_packages.append(server.args[1])
        return mcp_packages

    def _normalize_search_path(self, path: str) -> str:
        """Normalize search path to absolute local path.

        Converts relative/virtual paths to absolute paths for search operations.

        Args:
            path: Path to normalize (".", relative, or absolute)

        Returns:
            Absolute local path
        """
        if path == ".":
            return str(self._work_dir)
        if not path.startswith("/"):
            return str(self._work_dir / path)
        return path

    async def setup_sandbox_workspace(self) -> str | None:
        """Create local workspace directories.

        Returns:
            None (no snapshot name needed for local execution)
        """
        logger.info("Setting up local workspace")

        # Create workspace directories
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._tools_dir.mkdir(parents=True, exist_ok=True)
        (self._tools_dir / "docs").mkdir(parents=True, exist_ok=True)
        self._results_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._code_dir.mkdir(parents=True, exist_ok=True)

        # Generate sandbox ID based on working directory
        self.sandbox_id = hashlib.sha256(str(self._work_dir).encode()).hexdigest()[:8]

        logger.info(
            "Local workspace ready",
            work_dir=str(self._work_dir),
            sandbox_id=self.sandbox_id,
        )
        return None

    async def setup_tools_and_mcp(self, snapshot_name: str | None) -> None:
        """Install tool modules and setup MCP server configurations.

        Args:
            snapshot_name: Not used for local execution (kept for API compatibility)
        """
        logger.info("Setting up tools and MCP servers")

        # Generate and install tool modules
        await self._install_tool_modules()

        # Initialize MCP server sessions
        self.mcp_server_sessions: dict[str, Any] = {}
        await self._setup_mcp_server_sessions()

        logger.info("Tools and MCP servers ready", sandbox_id=self.sandbox_id)

    async def setup(self) -> None:
        """Set up the sandbox environment.

        For async initialization, use setup_sandbox_workspace() and
        setup_tools_and_mcp() separately via Session.initialize().
        """
        await self.setup_sandbox_workspace()
        await self.setup_tools_and_mcp(None)

        # Initialize ipybox CodeExecutor
        await self._init_code_executor()

        logger.info("Sandbox setup complete", sandbox_id=self.sandbox_id)

    async def _init_code_executor(self) -> None:
        """Initialize the ipybox CodeExecutor."""
        logger.info("Initializing ipybox CodeExecutor")

        # Set up environment variables for the kernel
        kernel_env = {
            "PYTHONPATH": str(self._work_dir),
        }

        # Add environment variables from MCP server configs
        for server in self.config.mcp.servers:
            if not server.enabled:
                continue
            if hasattr(server, "env") and server.env:
                for key, value in server.env.items():
                    if value.startswith("${") and value.endswith("}"):
                        var_name = value[2:-1]
                        resolved_value = os.getenv(var_name)
                        if resolved_value:
                            kernel_env[key] = resolved_value
                    else:
                        kernel_env[key] = value

        # Create CodeExecutor with images directory
        self._code_executor = CodeExecutor(
            kernel_env=kernel_env,
            images_dir=self._results_dir,
            log_level="WARNING",
        )
        await self._code_executor.start()

        logger.info("ipybox CodeExecutor initialized")

    async def reconnect(self, sandbox_id: str) -> None:
        """Reconnect to an existing sandbox.

        For local execution, this simply reinitializes the code executor.

        Args:
            sandbox_id: The sandbox ID (used for verification)
        """
        logger.info("Reconnecting to sandbox", sandbox_id=sandbox_id)

        self.sandbox_id = sandbox_id

        # Ensure workspace directories exist
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._tools_dir.mkdir(parents=True, exist_ok=True)
        self._results_dir.mkdir(parents=True, exist_ok=True)

        # Reinitialize code executor
        await self._init_code_executor()

        # Initialize MCP server sessions
        self.mcp_server_sessions = {}
        await self._setup_mcp_server_sessions()

        logger.info("Sandbox reconnected", sandbox_id=self.sandbox_id)

    async def stop_sandbox(self) -> None:
        """Stop the code executor.

        Used for session persistence - stops the executor so it can be
        restarted quickly on the next session.
        """
        if self._code_executor:
            try:
                await self._code_executor.stop()
                logger.info("CodeExecutor stopped", sandbox_id=self.sandbox_id)
            except Exception as e:
                logger.warning(
                    "Failed to stop CodeExecutor",
                    sandbox_id=self.sandbox_id,
                    error=str(e),
                )
            self._code_executor = None

    SKILLS_MANIFEST_FILENAME = ".skills_manifest.json"

    async def compute_skills_manifest(self, local_skill_roots: list[str]) -> dict[str, Any]:
        """Compute a cheap manifest for skills contents.

        Used to detect changes and avoid re-uploading skills on every startup.

        Args:
            local_skill_roots: List of local directories to scan, in priority order.
                Later directories override earlier ones.

        Returns:
            Manifest dict with "version" and "files".
        """
        return await self._compute_skills_manifest(local_skill_roots)

    async def _compute_skills_manifest(self, local_skill_roots: list[str]) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            files: dict[str, dict[str, int]] = {}
            seen_skill_names: set[str] = set()

            for root_str in local_skill_roots:
                root = Path(root_str).expanduser()
                if not root.exists():
                    continue

                for skill_dir in root.iterdir():
                    if not skill_dir.is_dir():
                        continue

                    if not (skill_dir / "SKILL.md").exists():
                        continue

                    # Later sources override earlier ones
                    skill_name = skill_dir.name
                    if skill_name in seen_skill_names:
                        prefix = f"{skill_name}/"
                        for key in list(files.keys()):
                            if key.startswith(prefix):
                                del files[key]
                    else:
                        seen_skill_names.add(skill_name)

                    for file_path in skill_dir.iterdir():
                        if not file_path.is_file():
                            continue

                        rel_path = f"{skill_dir.name}/{file_path.name}"
                        stat = file_path.stat()
                        files[rel_path] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

            payload = "\n".join(f"{p}:{meta['size']}:{meta['mtime_ns']}" for p, meta in sorted(files.items()))
            version = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            return {"version": version, "files": files}

        return await asyncio.to_thread(build)

    async def sync_skills(
        self,
        local_skills_dirs: list[tuple[str, str]],
        *,
        reusing_sandbox: bool,
        on_progress: Callable[[str], None] | None = None,
    ) -> bool:
        """Ensure skills are present in the sandbox.

        Computes a local manifest and compares it to the sandbox manifest.
        Uploads only when the sandbox is new or the manifest version differs.

        Args:
            local_skills_dirs: Ordered list of (local_path, sandbox_path) sources.
                Later entries override earlier ones.
            reusing_sandbox: Whether we reconnected to an existing sandbox.
            on_progress: Optional callback for reporting progress.

        Returns:
            True if an upload occurred.
        """
        local_roots = [local_dir for local_dir, _ in local_skills_dirs]
        local_manifest = await self._compute_skills_manifest(local_roots)

        if not local_manifest.get("files"):
            return False

        sandbox_base = local_skills_dirs[-1][1].rstrip("/")
        manifest_path = Path(f"{sandbox_base}/{self.SKILLS_MANIFEST_FILENAME}")

        remote_manifest: dict[str, Any] | None = None
        if manifest_path.exists():
            try:
                async with aiofiles.open(manifest_path) as f:
                    content = await f.read()
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    remote_manifest = parsed
            except (json.JSONDecodeError, OSError):
                remote_manifest = None

        remote_version = remote_manifest.get("version") if remote_manifest else None
        local_version = local_manifest.get("version")

        should_upload = (not reusing_sandbox) or (remote_version != local_version)
        if should_upload:
            if on_progress:
                on_progress("Uploading skills...")
            await self._upload_skills(local_skills_dirs)
            return True

        return False

    async def _upload_skills(self, local_skills_dirs: list[tuple[str, str]]) -> None:
        """Upload skill files from local filesystem to sandbox directory.

        Skills are markdown-based instruction files that extend agent capabilities.
        Each skill is a directory containing a SKILL.md file with YAML frontmatter.

        Skills from later local directories override earlier ones.

        Args:
            local_skills_dirs: List of (local_path, sandbox_path) tuples.
        """
        local_roots = [local_dir for local_dir, _ in local_skills_dirs]
        manifest = await self._compute_skills_manifest(local_roots)

        if not manifest.get("files"):
            logger.debug("No skills found; skipping upload")
            return

        uploaded_skill_names: set[str] = set()
        total_skills_uploaded = 0

        async def list_skill_dirs(local_root: Path) -> list[Path]:
            def _list() -> list[Path]:
                dirs: list[Path] = []
                for entry in local_root.iterdir():
                    if not entry.is_dir():
                        continue
                    if not (entry / "SKILL.md").exists():
                        continue
                    dirs.append(entry)
                return dirs

            return await asyncio.to_thread(_list)

        async def list_skill_files(skill_dir: Path) -> list[Path]:
            def _list() -> list[Path]:
                return [p for p in skill_dir.iterdir() if p.is_file()]

            return await asyncio.to_thread(_list)

        for local_dir, sandbox_dir in local_skills_dirs:
            local_path = Path(local_dir).expanduser()
            if not local_path.exists():
                logger.debug(f"Skills directory not found: {local_path}")
                continue

            # Create sandbox skills directory
            sandbox_path = Path(sandbox_dir)
            sandbox_path.mkdir(parents=True, exist_ok=True)

            # Upload all skill directories
            for skill_dir in await list_skill_dirs(local_path):
                skill_name = skill_dir.name
                if skill_name in ("", ".", ".."):
                    continue

                sandbox_skill_dir = sandbox_path / skill_name

                # Later sources override earlier ones; delete the existing directory
                if skill_name in uploaded_skill_names:
                    if sandbox_skill_dir.exists():
                        await asyncio.to_thread(lambda p: p.rmtree() if hasattr(p, 'rmtree') else __import__('shutil').rmtree(p), sandbox_skill_dir)

                sandbox_skill_dir.mkdir(parents=True, exist_ok=True)
                uploaded_skill_names.add(skill_name)
                total_skills_uploaded += 1

                for file_path in await list_skill_files(skill_dir):
                    sandbox_file = sandbox_skill_dir / file_path.name
                    async with aiofiles.open(str(file_path), "rb") as f:
                        content = await f.read()
                    async with aiofiles.open(str(sandbox_file), "wb") as f:
                        await f.write(content)

        # Persist manifest
        manifest_dir = Path(local_skills_dirs[-1][1].rstrip("/"))
        manifest_path = manifest_dir / self.SKILLS_MANIFEST_FILENAME
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(str(manifest_path), "w") as f:
            await f.write(json.dumps(manifest, sort_keys=True))

        logger.info(
            "Uploaded skills to sandbox",
            skill_count=total_skills_uploaded,
            file_count=len(manifest.get("files", {})),
            manifest_path=str(manifest_path),
        )

    async def _install_tool_modules(self) -> None:
        """Generate and install tool modules from MCP servers."""
        logger.info("Installing tool modules")

        # Ensure tools directory exists
        self._tools_dir.mkdir(parents=True, exist_ok=True)

        # 1. MCP client module
        mcp_client_code = self.tool_generator.generate_mcp_client_code(
            self.config.mcp.servers
        )
        mcp_client_path = self._tools_dir / "mcp_client.py"
        async with aiofiles.open(str(mcp_client_path), "w") as f:
            await f.write(mcp_client_code)

        logger.info("MCP client module installed", path=str(mcp_client_path))

        # 2. Tool modules and documentation
        if self.mcp_registry:
            tools_by_server = self.mcp_registry.get_all_tools()

            for server_name, tools in tools_by_server.items():
                # Generate Python module
                module_code = self.tool_generator.generate_tool_module(
                    server_name, tools
                )
                module_path = self._tools_dir / f"{server_name}.py"
                async with aiofiles.open(str(module_path), "w") as f:
                    await f.write(module_code)

                logger.info(
                    "Tool module installed",
                    server=server_name,
                    path=str(module_path),
                    tool_count=len(tools),
                )

                # Generate documentation for each tool
                doc_dir = self._tools_dir / "docs" / server_name
                doc_dir.mkdir(parents=True, exist_ok=True)

                for tool in tools:
                    doc = self.tool_generator.generate_tool_documentation(tool)
                    doc_path = doc_dir / f"{tool.name}.md"
                    async with aiofiles.open(str(doc_path), "w") as f:
                        await f.write(doc)

        # 3. __init__.py for tools package
        init_content = '"""Auto-generated tool modules from MCP servers."""\n'
        init_path = self._tools_dir / "__init__.py"
        async with aiofiles.open(str(init_path), "w") as f:
            await f.write(init_content)

        logger.info("Tool modules installation complete")

    async def _setup_mcp_server_sessions(self) -> None:
        """Setup MCP server session configurations."""
        logger.info("Setting up MCP server sessions")

        self.mcp_server_sessions = {}

        for server in self.config.mcp.servers:
            if not server.enabled:
                continue
            if server.transport != "stdio":
                logger.warning(
                    f"Skipping non-stdio server {server.name}",
                    transport=server.transport
                )
                continue

            # Build the command to start the MCP server
            if server.command == "npx":
                cmd_parts = [server.command, *server.args]
                cmd = " ".join(cmd_parts)
            else:
                cmd = f"{server.command} {' '.join(server.args)}"

            # Add environment variables if specified
            env_vars = {}
            if hasattr(server, "env") and server.env:
                for key, value in server.env.items():
                    if value.startswith("${") and value.endswith("}"):
                        var_name = value[2:-1]
                        resolved_value = os.getenv(var_name)
                        if resolved_value:
                            env_vars[key] = resolved_value
                    else:
                        env_vars[key] = value

            session_name = f"mcp-{server.name}"

            self.mcp_server_sessions[server.name] = {
                "session_name": session_name,
                "command": cmd,
                "env": env_vars,
                "started": False
            }

            logger.info(
                "MCP server session configured",
                server=server.name,
                session=session_name
            )

        logger.info(
            "MCP server configuration complete",
            servers=list(self.mcp_server_sessions.keys())
        )

    def _detect_missing_imports(self, stderr: str) -> list[str]:
        """Extract missing module names from ImportError/ModuleNotFoundError.

        Args:
            stderr: Standard error output from code execution

        Returns:
            List of missing package names (base package only, e.g., 'foo' from 'foo.bar')
        """
        import re
        patterns = [
            r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]",
            r"ImportError: No module named ['\"]([^'\"]+)['\"]",
        ]

        matches = []
        for pattern in patterns:
            matches.extend(re.findall(pattern, stderr))

        # Handle submodule imports and deduplicate
        base_packages = list({m.split(".")[0] for m in matches})

        if base_packages:
            logger.info(
                "Detected missing imports",
                packages=base_packages,
            )

        return base_packages

    async def _install_package(self, package: str) -> bool:
        """Install a Python package locally.

        Args:
            package: Package name to install

        Returns:
            True if installation succeeded, False otherwise
        """
        try:
            logger.info(f"Auto-installing missing package: {package}")
            result = subprocess.run(
                ["uv", "pip", "install", "-q", package],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                logger.info(f"Successfully installed package: {package}")
                return True
            logger.warning(f"Failed to install package: {package}, exit_code={result.returncode}")
            return False
        except Exception as e:
            logger.warning(f"Failed to install {package}: {e}")
            return False

    async def execute(
        self, code: str, timeout: int | None = None, *, auto_install: bool = True, max_retries: int = 2
    ) -> ExecutionResult:
        """Execute Python code in the ipybox kernel with optional auto-install for missing dependencies.

        Args:
            code: Python code to execute
            timeout: Optional timeout in seconds
            auto_install: Whether to automatically install missing packages on ImportError (default: True)
            max_retries: Maximum number of retries after auto-installing packages (default: 2)

        Returns:
            ExecutionResult with execution details
        """
        if not self._code_executor:
            raise RuntimeError("CodeExecutor not initialized. Call setup() first.")

        self.execution_count += 1
        execution_id = f"exec_{self.execution_count:04d}"
        code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]

        logger.info(
            "Executing code",
            execution_id=execution_id,
            code_hash=code_hash,
            code_length=len(code),
            auto_install=auto_install,
        )

        start_time = time.time()

        try:
            # Get list of files before execution
            files_before = await self._list_result_files()

            # Execute code using ipybox
            timeout_val = timeout or self.config.security.max_execution_time

            try:
                result = await self._code_executor.execute(code, timeout=timeout_val)

                stdout = result.text or ""
                stderr = ""
                success = True

                # Handle images from execution
                charts = []
                for img_path in result.images:
                    # Read image and convert to base64
                    async with aiofiles.open(str(img_path), "rb") as f:
                        img_data = await f.read()
                    import base64
                    charts.append(ChartData(
                        type="image",
                        title=img_path.name,
                        png_base64=base64.b64encode(img_data).decode(),
                    ))

                if charts:
                    logger.info(f"Captured {len(charts)} image(s) from execution")

            except CodeExecutionError as e:
                stdout = ""
                stderr = str(e)
                success = False
                charts = []

            except asyncio.TimeoutError:
                stdout = ""
                stderr = f"Code execution timed out after {timeout_val} seconds"
                success = False
                charts = []

            # Get files after execution
            files_after = await self._list_result_files()

            # Determine file changes
            files_created = [f for f in files_after if f not in files_before]
            files_modified: list[str] = []

            duration = time.time() - start_time

            execution_result = ExecutionResult(
                success=success,
                stdout=stdout,
                stderr=stderr,
                duration=duration,
                files_created=files_created,
                files_modified=files_modified,
                execution_id=execution_id,
                code_hash=code_hash,
                charts=charts,
            )

            # Auto-install missing packages and retry if enabled
            if not success and auto_install and max_retries > 0:
                missing_packages = self._detect_missing_imports(stderr)
                if missing_packages:
                    logger.info(
                        "Attempting auto-install and retry",
                        execution_id=execution_id,
                        missing_packages=missing_packages,
                        retries_remaining=max_retries,
                    )

                    # Install missing packages
                    for package in missing_packages:
                        await self._install_package(package)

                    # Reset the code executor to pick up new packages
                    await self._code_executor.reset()

                    # Retry execution with decremented retry count
                    return await self.execute(
                        code=code,
                        timeout=timeout,
                        auto_install=auto_install,
                        max_retries=max_retries - 1
                    )

            logger.info(
                "Code execution completed",
                execution_id=execution_id,
                success=success,
                duration=duration,
                files_created=len(files_created),
                charts_captured=len(charts),
            )

            return execution_result

        except Exception as e:
            duration = time.time() - start_time

            logger.error(
                "Code execution failed",
                execution_id=execution_id,
                error=str(e),
                duration=duration,
            )

            return ExecutionResult(
                success=False,
                stdout="",
                stderr=str(e),
                duration=duration,
                files_created=[],
                files_modified=[],
                execution_id=execution_id,
                code_hash=code_hash,
                charts=[],
            )

    async def execute_bash_command(
        self, command: str, working_dir: str | None = None, timeout: int = 60, *, background: bool = False
    ) -> dict[str, Any]:
        """Execute a bash command locally.

        Args:
            command: Bash command to execute
            working_dir: Working directory for command execution (default: workspace directory)
            timeout: Maximum execution time in seconds (default: 60)
            background: Run command in background (not fully implemented yet)

        Returns:
            Dictionary with success, stdout, stderr, exit_code, bash_id, command_hash
        """
        try:
            # Generate bash execution ID for tracking
            self.bash_execution_count += 1
            bash_id = f"bash_{self.bash_execution_count:04d}"
            command_hash = hashlib.sha256(command.encode()).hexdigest()[:16]
            from datetime import UTC, datetime
            timestamp = datetime.now(tz=UTC).isoformat()

            work_dir = working_dir or str(self._work_dir)

            logger.info(
                "Executing bash command",
                bash_id=bash_id,
                command_hash=command_hash,
                command=command[:100],
                working_dir=work_dir,
            )

            # Create a shell script with metadata header for logging
            script_content = textwrap.dedent(f"""\
                #!/bin/bash
                # Bash Execution Log
                # ID: {bash_id}
                # Working Directory: {work_dir}
                # Timestamp: {timestamp}
                # Command Hash: {command_hash}

                set -e  # Exit on error
                {command}
            """)

            # Write script to code/ directory
            self._code_dir.mkdir(parents=True, exist_ok=True)
            script_path = self._code_dir / f"{bash_id}.sh"
            async with aiofiles.open(str(script_path), "w") as f:
                await f.write(script_content)

            # Execute the script
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["bash", str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=work_dir,
                )

                return {
                    "success": result.returncode == 0,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.returncode,
                    "bash_id": bash_id,
                    "command_hash": command_hash,
                }

            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout} seconds",
                    "exit_code": 124,
                    "bash_id": bash_id,
                    "command_hash": command_hash,
                }

        except Exception as e:
            logger.error(f"Failed to execute bash command: {e}", exc_info=True)
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Exception during bash execution: {e!s}",
                "exit_code": -1,
                "bash_id": getattr(self, "_last_bash_id", None),
                "command_hash": None,
            }

    async def _list_result_files(self) -> list[str]:
        """List files in the results directory.

        Returns:
            List of file paths relative to workspace (e.g., "results/file.csv")
        """
        try:
            if not self._results_dir.exists():
                return []
            files = []
            for entry in self._results_dir.iterdir():
                if entry.is_file():
                    files.append(f"results/{entry.name}")
            return files
        except Exception as e:
            logger.warning(f"Error listing result files: {e}")
            return []

    async def adownload_file_bytes(self, filepath: str) -> bytes | None:
        """Download raw bytes from local file.

        Returns:
            Bytes if downloaded, or None if missing.
        """
        try:
            path = self._resolve_path(filepath)
            if not path.exists():
                return None
            async with aiofiles.open(str(path), "rb") as f:
                return await f.read()
        except Exception as e:
            logger.debug("Failed to download file bytes", filepath=filepath, error=str(e))
            return None

    async def aread_file_text(self, filepath: str) -> str | None:
        """Read a UTF-8 text file from the local filesystem.

        This path is safe to retry automatically.
        """
        content_bytes = await self.adownload_file_bytes(filepath)
        if not content_bytes:
            return None
        try:
            return content_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            logger.debug("Failed to decode file as utf-8", filepath=filepath, error=str(e))
            return None

    async def aupload_file_bytes(self, filepath: str, content: bytes) -> bool:
        """Upload raw bytes to the local filesystem.

        This path is safe to retry automatically because uploads overwrite the target.
        """
        if self.config.filesystem.enable_path_validation and not self.validate_path(filepath):
            logger.error(f"Access denied: {filepath} is not in allowed directories")
            return False

        try:
            path = self._resolve_path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(str(path), "wb") as f:
                await f.write(content)
            return True
        except Exception as e:
            logger.debug("Failed to upload file bytes", filepath=filepath, error=str(e))
            return False

    async def awrite_file_text(self, filepath: str, content: str) -> bool:
        """Write UTF-8 text to a local file (overwrites).

        This path is safe to retry automatically.
        """
        try:
            return await self.aupload_file_bytes(filepath, content.encode("utf-8"))
        except UnicodeEncodeError as e:
            logger.debug("Failed to encode file as utf-8", filepath=filepath, error=str(e))
            return False

    async def aread_file_range(self, file_path: str, offset: int = 0, limit: int = 2000) -> str | None:
        """Read a specific range of lines from a UTF-8 text file.

        Args:
            file_path: Path to the file.
            offset: Line offset (0-indexed).
            limit: Maximum number of lines.
        """
        content = await self.aread_file_text(file_path)
        if content is None:
            return None

        lines = content.splitlines()
        start = max(0, offset)
        end = start + limit
        return "\n".join(lines[start:end])

    def _resolve_path(self, filepath: str) -> Path:
        """Resolve a virtual or relative path to an absolute local path.

        Args:
            filepath: Virtual, relative, or absolute path

        Returns:
            Resolved absolute Path object
        """
        # Handle absolute paths
        if filepath.startswith("/"):
            # Check if it's a virtual path within workspace
            work_dir_str = str(self._work_dir)
            if filepath.startswith(work_dir_str):
                return Path(filepath)
            # Virtual path: /foo -> workspace/foo
            return self._work_dir / filepath.lstrip("/")

        # Relative path
        return self._work_dir / filepath

    def normalize_path(self, path: str) -> str:
        """Normalize virtual path to absolute local path (input normalization).

        Converts agent's virtual paths to real local paths:
            "/" or "." or "" -> {working_directory}
            "/results/file.txt" -> {working_directory}/results/file.txt
            "data/file.txt" -> {working_directory}/data/file.txt
            "{working_directory}/file.txt" -> unchanged
            "/tmp/file.txt" -> unchanged

        Args:
            path: Virtual or relative path from agent

        Returns:
            Absolute local path
        """
        work_dir = self.config.filesystem.working_directory

        if path in (None, "", ".", "/"):
            return work_dir

        path = path.strip()

        # Already in allowed directories - keep as is
        for allowed_dir in self.config.filesystem.allowed_directories:
            if path.startswith(allowed_dir):
                return str(Path(path))

        # Virtual absolute path: /foo -> /home/daytona/foo
        if path.startswith("/"):
            return str(Path(f"{work_dir}{path}"))

        # Relative path: foo -> /home/daytona/foo
        return str(Path(f"{work_dir}/{path}"))

    def virtualize_path(self, path: str) -> str:
        """Convert real local path to virtual path (output normalization).

        Strips working_directory prefix from paths returned to agent:
            {working_directory}/results/file.txt -> /results/file.txt
            {working_directory}/tools/docs/foo.md -> /tools/docs/foo.md
            /tmp/file.txt -> /tmp/file.txt (unchanged)

        Args:
            path: Absolute local path

        Returns:
            Virtual path for agent consumption
        """
        work_dir = self.config.filesystem.working_directory

        if path.startswith(work_dir + "/"):
            return path[len(work_dir):]  # Strip prefix, keep leading /
        if path == work_dir:
            return "/"

        return path  # /tmp or other paths unchanged

    def validate_path(self, filepath: str) -> bool:
        """Validate if a path is within allowed directories.

        Args:
            filepath: Path to validate (virtual or absolute)

        Returns:
            True if path is allowed, False otherwise
        """
        if not self.config.filesystem.enable_path_validation:
            return True

        # Normalize the path first
        normalized_path = self.normalize_path(filepath)

        # Check against allowed directories
        for allowed_dir in self.config.filesystem.allowed_directories:
            if normalized_path == allowed_dir or normalized_path.startswith(allowed_dir + "/"):
                return True

        logger.warning(
            "Path validation failed",
            path=filepath,
            normalized_path=normalized_path,
            allowed_dirs=self.config.filesystem.allowed_directories,
        )
        return False

    def validate_and_normalize_path(self, path: str) -> tuple[str, str | None]:
        """Normalize path and validate access.

        Combines path normalization and validation into a single operation.

        Args:
            path: Virtual or relative path from agent

        Returns:
            Tuple of (normalized_path, error_message_or_none)
        """
        normalized = self.normalize_path(path)
        if self.config.filesystem.enable_path_validation and not self.validate_path(normalized):
            return normalized, f"Access denied: {path} is not in allowed directories"
        return normalized, None

    async def als_directory(self, directory: str = ".") -> list[dict[str, Any]]:
        """List contents of a directory.

        Returns entries as dicts with at least: name, path, is_dir.
        """
        try:
            if self.config.filesystem.enable_path_validation and not self.validate_path(directory):
                logger.error(f"Access denied: {directory} is not in allowed directories")
                return []

            path = self._resolve_path(directory)
            if not path.exists():
                return []

            results: list[dict[str, Any]] = []
            for entry in path.iterdir():
                entry_path = f"{directory}/{entry.name}" if directory != "." else entry.name
                results.append({
                    "name": entry.name,
                    "path": entry_path,
                    "is_dir": entry.is_dir(),
                })
            return results
        except Exception as e:
            logger.debug("Error listing directory", directory=directory, error=str(e))
            return []

    async def acreate_directory(self, dirpath: str) -> bool:
        """Create a directory in the local filesystem."""
        try:
            if self.config.filesystem.enable_path_validation and not self.validate_path(dirpath):
                logger.error(f"Access denied: {dirpath} is not in allowed directories")
                return False

            path = self._resolve_path(dirpath)
            path.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            logger.debug("Failed to create directory", dirpath=dirpath, error=str(e))
            return False

    async def aedit_file_text(
        self,
        filepath: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        """Async edit for tools; safe to retry underlying I/O.

        This does not retry the logical edit itself; it only makes file I/O resilient.
        """
        try:
            if self.config.filesystem.enable_path_validation and not self.validate_path(filepath):
                return {
                    "success": False,
                    "error": f"Access denied: {filepath} is not in allowed directories",
                }

            content = await self.aread_file_text(filepath)
            if content is None:
                return {"success": False, "error": "File not found"}

            if old_string == new_string:
                return {"success": False, "error": "old_string and new_string must be different"}

            if old_string not in content:
                return {"success": False, "error": f"old_string not found in file: {filepath}"}

            if not replace_all:
                occurrences = content.count(old_string)
                if occurrences > 1:
                    return {
                        "success": False,
                        "error": "old_string found multiple times and requires more code context to uniquely identify the intended match",
                    }

            updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)

            if updated == content:
                return {"success": False, "error": "Edit produced no changes"}

            write_ok = await self.awrite_file_text(filepath, updated)
            if not write_ok:
                return {"success": False, "error": "Failed to write updated file"}

            return {
                "success": True,
                "message": "File edited successfully",
            }

        except Exception as e:
            logger.debug("Async edit_file failed", filepath=filepath, error=str(e))
            return {"success": False, "error": f"Edit operation failed: {e!s}"}

    async def aglob_files(self, pattern: str, path: str = ".") -> list[str]:
        """Async glob; safe to retry automatically."""
        try:
            if self.config.filesystem.enable_path_validation and not self.validate_path(path):
                logger.error(f"Access denied: {path} is not in allowed directories")
                return []

            search_path = self._resolve_path(path)

            if "**" not in pattern and "/" not in pattern:
                pattern = f"**/{pattern}"

            def _glob() -> list[str]:
                full_pattern = str(search_path / pattern)
                matches = Path(search_path).glob(pattern.replace("**", "**"))
                files = [str(f) for f in matches if f.is_file()]
                # Sort by modification time
                try:
                    files_with_mtime = [(f, Path(f).stat().st_mtime) for f in files]
                    files_with_mtime.sort(key=lambda x: x[1], reverse=True)
                    return [f for f, _ in files_with_mtime]
                except OSError:
                    return files

            results = await asyncio.to_thread(_glob)
            return results

        except Exception as e:
            logger.debug("Async glob failed", pattern=pattern, path=path, error=str(e))
            return []

    async def agrep_content(
        self,
        pattern: str,
        path: str = ".",
        output_mode: str = "files_with_matches",
        glob: str | None = None,
        type: str | None = None,  # noqa: A002 - matches ripgrep's --type flag
        *,
        case_insensitive: bool = False,
        show_line_numbers: bool = True,
        lines_after: int | None = None,
        lines_before: int | None = None,
        lines_context: int | None = None,
        multiline: bool = False,
        head_limit: int | None = None,
        offset: int = 0,
    ) -> Any:
        """Async ripgrep; safe to retry automatically."""
        try:
            if self.config.filesystem.enable_path_validation and not self.validate_path(path):
                logger.error(f"Access denied: {path} is not in allowed directories")
                return []

            search_path = self._resolve_path(path)

            cmd = ["rg"]
            if output_mode == "files_with_matches":
                cmd.append("-l")
            elif output_mode == "count":
                cmd.append("-c")

            if case_insensitive:
                cmd.append("-i")

            if output_mode == "content" and show_line_numbers:
                cmd.append("-n")

            if lines_before:
                cmd.extend(["-B", str(lines_before)])
            if lines_after:
                cmd.extend(["-A", str(lines_after)])
            if lines_context:
                cmd.extend(["-C", str(lines_context)])

            if multiline:
                cmd.extend(["-U", "--multiline-dotall"])

            if glob:
                cmd.extend(["--glob", glob])
            if type:
                cmd.extend(["--type", type])

            cmd.append(pattern)
            cmd.append(str(search_path))

            def _run_grep() -> str:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                return result.stdout

            output = await asyncio.to_thread(_run_grep)
            output = output.strip()

            if not output:
                return []

            if output_mode == "count":
                count_results: list[tuple[str, int]] = []
                for line in output.split("\n"):
                    if ":" in line:
                        parts = line.rsplit(":", 1)
                        if len(parts) == 2:
                            try:
                                count_results.append((parts[0], int(parts[1])))
                            except ValueError:
                                count_results.append((line, 0))
                    else:
                        count_results.append((line, 0))

                if offset > 0:
                    count_results = count_results[offset:]
                if head_limit:
                    count_results = count_results[:head_limit]
                return count_results

            results_strs = output.split("\n")
            if offset > 0:
                results_strs = results_strs[offset:]
            if head_limit:
                results_strs = results_strs[:head_limit]
            return results_strs

        except Exception as e:
            logger.debug("Async grep failed", pattern=pattern, path=path, error=str(e))
            return []

    async def cleanup(self) -> None:
        """Clean up and stop the code executor."""
        logger.info("Cleaning up sandbox", sandbox_id=self.sandbox_id)

        if self._code_executor:
            try:
                await self._code_executor.stop()
                logger.info("CodeExecutor stopped", sandbox_id=self.sandbox_id)
            except Exception as e:
                logger.error(f"Error stopping CodeExecutor: {e}")
            self._code_executor = None

        self.sandbox_id = None

    async def __aenter__(self) -> "PTCSandbox":
        """Async context manager entry."""
        await self.setup()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        await self.cleanup()
