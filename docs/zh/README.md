# Open PTC Agent

[English](../../README.md) | [中文](README.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![LangChain](https://img.shields.io/badge/LangChain-1c3c3c?logo=langchain&logoColor=white)](https://github.com/langchain-ai/langchain)
[![GitHub stars](https://img.shields.io/github/stars/Chen-zexi/open-ptc-agent?style=social)](https://github.com/Chen-zexi/open-ptc-agent/stargazers)

[快速开始](#快速开始) | [CLI 参考](#cli-参考) | [配置指南](CONFIGURATION.md) | [更新日志](../CHANGELOG.md) | [路线图](#路线图)

<video src="https://github.com/user-attachments/assets/cca8c6ee-0c6f-4a97-ad7d-08bad250c006" controls width="800"></video>

*演示：使用 DeepSeek V3.2 分析 2 年的 NVDA、AMD 和 SPY 股票数据（15,000+ 行原始 JSON）*

## 什么是程序化工具调用？

本项目是 Anthropic 最近推出的[程序化工具调用 (PTC)](https://www.anthropic.com/engineering/advanced-tool-use) 的开源实现，相比传统的 JSON 工具调用，Agent 通过代码执行来调用工具（包括 MCP 工具）。这一范式也在他们早期的博客 [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) 中有所介绍。

## 为什么选择 PTC？

1. LLM 擅长编写代码！它们在理解上下文、推理数据流和生成精确逻辑方面表现出色。PTC 让它们发挥所长——编写代码来编排整个工作流程，而不是一次处理一个工具调用。

2. 传统工具调用会将完整结果返回到模型的上下文窗口。例如，获取 10 只股票一年的每日价格意味着 2,500 多个 OHLCV 数据点——仅计算投资组合摘要就需要数万个 token。使用 PTC，代码在沙箱中运行，在本地处理数据，只有最终输出返回给模型。结果：token 减少 85-98%。

3. PTC 在处理大量结构化数据、时间序列数据（如金融市场数据）以及需要进一步数据处理的场景中表现尤为出色——在将结果返回给模型之前进行过滤、聚合、转换或可视化。

## 工作原理

```
User Task
    |
    v
+-------------------+
|    PTCAgent       |  工具发现 -> 编写 Python 代码
+-------------------+
    |       ^
    v       |
+-------------------+
|  Daytona Sandbox  |  执行代码
|  +-------------+  |
|  | MCP Tools   |  |  tool() -> 处理 / 过滤 / 聚合 -> 输出到 data/ 目录
|  | (Python)    |  |
|  +-------------+  |
+-------------------+
    |
    v
+-------------------+
|   最终交付物      |  文件和数据可从沙箱下载
+-------------------+
```

> **基于 [LangChain DeepAgents](https://github.com/langchain-ai/deepagents) 构建** - 本项目使用了 DeepAgents 的许多组件，CLI 功能基于 deepagent-cli 启动。特别感谢 LangChain 团队！

## 最新更新

- **交互式 CLI** - 新增 `ptc-agent` 命令，提供基于终端的交互界面，支持沙盒持久化、Plan模式、主题，和快捷切换模型
- **后台子 Agent 执行** - 子 Agent 使用 Task ID（Task-1、Task-2 等）异步运行。主 Agent 在子 Agent 并行执行时继续工作。已完成的结果被缓存，并通知 Agent 通过 `task_output()` 获取
- **任务监控** - `wait()` 阻塞等待任务完成；`task_output()` 获取结果或显示进度
- **Agent Skills** - 通过开放的 [Agent Skills](https://agentskills.io) 标准提供可扩展能力
- **视觉/多模态支持** - 新增 `view_image` 工具，使具有视觉能力的 LLM 能够分析来自 URL、base64 数据或沙箱文件的图像


## 功能特性

- **通用 MCP 支持** - 自动将任何 MCP 服务器工具转换为 Python 函数
- **渐进式工具发现** - 按需发现工具；避免预先定义大量 token 的工具定义
- **自定义 MCP 上传** - 直接将 Python MCP 实现部署到沙箱会话中
- **Agent Skills** - 自定义工作流技能
- **增强文件工具** - 针对沙箱环境优化的 glob、grep 和其他文件操作工具
- **Daytona 后端** - 具有文件系统隔离和快照支持的安全代码执行
- **自动图片上传** - 图表和图像自动上传到云存储（Cloudflare R2、AWS S3、阿里云 OSS）
- **LangGraph 就绪** - 兼容 LangGraph Cloud/Studio 部署
- **多 LLM 支持** - 支持 Anthropic、OpenAI 以及您在 `llms.json` 中配置的任何 LLM 提供商

## 项目结构

```
├── libs/
│   ├── ptc-agent/             # 核心 Agent 库
│   │   └── ptc_agent/
│   │       ├── core/          # 沙箱、MCP 注册表、工具生成器、会话
│   │       ├── config/        # 配置类和加载器
│   │       ├── agent/         # PTCAgent、工具、提示词、中间件、子 Agent
│   │       └── utils/         # 云存储上传器
│   │
│   └── ptc-cli/               # 交互式 CLI 应用
│       └── ptc_cli/
│           ├── core/          # 状态、配置、主题
│           ├── commands/      # 斜杠命令、bash 执行
│           ├── display/       # Rich 终端渲染
│           ├── input/         # 提示、补全器、文件提及
│           └── streaming/     # 工具审批、执行
│
├── skills/                    # 演示技能（来自 Anthropic）
│   ├── pdf/                   # PDF 操作
│   ├── xlsx/                  # 电子表格操作
│   ├── docx/                  # 文档创建
│   ├── pptx/                  # 演示文稿创建
│   └── creating-financial-models/  # 财务建模
│
├── mcp_servers/               # 演示用 MCP 服务器实现
│   ├── yfinance_mcp_server.py
│   └── tickertick_mcp_server.py
│
├── example/                   # 演示 Notebook 和脚本
│   ├── PTC_Agent.ipynb
│   ├── Subagent_demo.ipynb
│   └── quickstart.py
│
├── config.yaml                # 主配置
└── llms.json                  # LLM 提供商定义
```

## 原生工具

Agent 可以访问原生工具以及来自 [deep-agent](https://github.com/langchain-ai/deepagents) 的中间件功能：

### 核心工具

| 工具 | 描述 | 关键参数 |
|------|------|----------|
| **execute_code** | 执行具有 MCP 工具访问权限的 Python | `code` |
| **Bash** | 运行 shell 命令 | `command`, `timeout`, `working_dir` |
| **Read** | 带行号读取文件 | `file_path`, `offset`, `limit` |
| **Write** | 写入/覆盖文件 | `file_path`, `content` |
| **Edit** | 精确字符串替换 | `file_path`, `old_string`, `new_string` |
| **Glob** | 文件模式匹配 | `pattern`, `path` |
| **Grep** | 内容搜索 (ripgrep) | `pattern`, `path`, `output_mode` |

### 中间件

| 中间件 | 描述 | 提供的工具 |
|--------|------|-----------|
| **SubagentsMiddleware** | 将专门任务委托给具有隔离执行的子 Agent | `task()` |
| **BackgroundSubagentMiddleware** | 异步子 Agent 执行，支持后台任务和基于通知的结果收集 | `wait()`, `task_output()` |
| **ViewImageMiddleware** | 将图像注入对话以供多模态 LLM 使用 | `view_image()` |
| **FilesystemMiddleware** | 文件操作 | `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `ls` |
| **TodoListMiddleware** | 任务规划和进度跟踪（自动启用） | `write_todos` |
| **SummarizationMiddleware** | 自动总结对话历史（自动启用） | - |

**可用的子 Agent（默认）：**
- `research` - 使用 Tavily 进行网络搜索 + think 工具进行战略性反思
- `general-purpose` - 完整的 execute_code、文件系统和视觉工具，用于复杂的多步骤任务

**后台执行模型：**
当 Agent 调用 `task()` 时，子 Agent 被分配顺序 ID（Task-1、Task-2 等）并在后台运行。主 Agent：
1. 立即收到包含 Task ID 的确认
2. 在子 Agent 并行执行时继续其他工作
3. 当任务完成时收到通知
4. 调用 `task_output()` 获取缓存的结果
5. 如需要，使用 `wait(task_number=N)` 阻塞等待特定任务

## MCP 集成

### 演示 MCP 服务器

演示包含 3 个在 `config.yaml` 中配置的已启用 MCP 服务器：

| 服务器 | 传输方式 | 工具数 | 用途 |
|--------|----------|--------|------|
| **tavily** | stdio (npx) | 4 | 网络搜索 |
| **yfinance** | stdio (python) | 21 | 股票价格、财务数据 |
| **tickertick** | stdio (python) | 7 | 金融新闻 |

### MCP 工具的呈现方式

**在提示中** - 工具摘要被注入到系统提示中：
```
tavily: Web search engine for finding current information
  - Module: tools/tavily.py
  - Tools: 4 tools available
  - Import: from tools.tavily import <tool_name>
```

**在沙箱中** - 生成完整的 Python 模块：
```
/home/daytona/
├── tools/
│   ├── mcp_client.py      # MCP 通信层
│   ├── tavily.py          # from tools.tavily import search
│   ├── yfinance.py        # from tools.yfinance import get_stock_history
│   └── docs/              # 自动生成的文档
│       ├── tavily/*.md
│       └── yfinance/*.md
├── results/               # Agent 输出
└── data/                  # 输入数据
```

**在代码中** - Agent 直接导入和使用工具：
```python
from tools.yfinance import get_stock_history
import pandas as pd

# 获取数据 - 保留在沙箱中
history = get_stock_history(ticker="AAPL", period="1y")

# 本地处理 - 不浪费 token
df = pd.DataFrame(history)
summary = {"mean": df["close"].mean(), "volatility": df["close"].std()}

# 只有摘要返回给模型
print(summary)
```

## Skills

[Agent Skills](https://agentskills.io) 是 Anthropic 发布的开放标准，用于将领域专业知识打包成可重用的指令和资源文件夹。Skills 通过**渐进式发现**动态加载 - 启动时仅加载元数据，完整内容按需加载。

### 包含的演示 Skills

演示中包含来自 [anthropics/skills](https://github.com/anthropics/skills) 的 Skills：

| Skill | 描述 |
|-------|------|
| **pdf** | PDF 操作 - 提取文本/表格、创建、合并/拆分、填写表单 |
| **xlsx** | 电子表格创建，支持公式、格式和数据分析 |
| **docx** | 文档创建、编辑和格式化 |
| **pptx** | 演示文稿创建、编辑和分析 |
| **creating-financial-models** | DCF 分析、敏感性测试、蒙特卡洛模拟 |

### 配置

Skills 默认启用，从以下位置加载：
1. 用户目录：`~/.ptc-agent/skills/`
2. 项目目录：`.ptc-agent/skills/`（或 `skills/` 用于旧版）

项目 Skills 会覆盖同名的用户 Skills。

```yaml
# config.yaml
skills:
  enabled: true
  user_skills_dir: "~/.ptc-agent/skills"
  project_skills_dir: ".ptc-agent/skills"
```

### 创建自定义 Skills

每个 Skill 是一个包含 `SKILL.md` 文件的文件夹，文件中包含 YAML 前言和指令：

```markdown
---
name: my-skill
description: "清晰描述此 Skill 的功能和使用场景"
---

# My Skill

Claude 激活此 Skill 时遵循的指令、工作流和示例。

## 指南
- 指南 1
- 指南 2
```

可以在 `SKILL.md` 旁边捆绑其他文件（如 `reference.md`、脚本），并根据需要引用。Skills 会上传到沙箱的 `/home/daytona/skills/<skill-name>/` 目录。

详细指南请参阅 [Anthropic 的 Skill 编写最佳实践](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)。

## 快速开始

### 前提条件

- Python 3.12+
- Node.js（用于 MCP 服务器）
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装

```bash
git clone https://github.com/Chen-zexi/open-ptc-agent.git
cd open-ptc-agent
uv sync
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### 最小配置

创建包含最少必需密钥的 `.env` 文件：

```bash
# 一个 LLM 提供商（选择一个）
ANTHROPIC_API_KEY=your-key
# 或
OPENAI_API_KEY=your-key
# 或
# 在 llms.json 和 config.yaml 中配置的任何模型
# 也可以在这里使用 Minimax 和 GLM 的 Coding 计划！

# Daytona（必需）
DAYTONA_API_KEY=your-key
```
从 [Daytona Dashboard](https://app.daytona.io/dashboard/keys) 获取您的 Daytona API 密钥。新用户可获得免费额度！

### 扩展配置

如需完整功能，添加可选密钥：

```bash
# MCP 服务器
TAVILY_API_KEY=your-key          # 网络搜索
ALPHA_VANTAGE_API_KEY=your-key   # 金融数据

# 云存储（选择一个提供商）
R2_ACCESS_KEY_ID=...             # Cloudflare R2
AWS_ACCESS_KEY_ID=...            # AWS S3
OSS_ACCESS_KEY_ID=...            # 阿里云 OSS

# 追踪（可选）
LANGSMITH_API_KEY=your-key
```

查看 `.env.example` 获取完整的配置选项列表。

### 运行 CLI

启动交互式 CLI：

```bash
ptc-agent
```

查看 **[ptc-cli 文档](../../libs/ptc-cli/README.md)** 了解所有命令和选项。

如需程序化使用 PTC Agent，请参阅 [ptc-agent 文档](../../libs/ptc-agent/README.md)。

### 演示 Notebook

Jupyter Notebook 示例：

- **[PTC_Agent.ipynb](../../example/PTC_Agent.ipynb)** - open-ptc-agent 快速演示
- **[Subagent_demo.ipynb](../../example/Subagent_demo.ipynb)** - 后台子 Agent 执行
- **[quickstart.py](../../example/quickstart.py)** - Python 脚本快速入门

您也可以选择使用 LangGraph API 来部署 Agent。

## 配置

项目使用两个配置文件：

- **config.yaml** - 主配置（LLM 选择、MCP 服务器、Daytona、安全、存储）
- **llms.json** - LLM 提供商定义

### 快速配置

在 `config.yaml` 中选择您的 LLM：

```yaml
llm:
  name: "claude-sonnet-4-5"  # 选项: claude-sonnet-4-5, gpt-5.1-codex-mini, gemini-3-pro
```

启用/禁用 MCP 服务器：

```yaml
mcp:
  servers:
    - name: "tavily"
      enabled: true  # 设置为 false 以禁用
```

有关完整配置选项，包括 Daytona 设置、安全策略和添加自定义 LLM 提供商，请参阅[配置指南](CONFIGURATION.md)。

## CLI 参考

`ptc-agent` 命令提供交互式终端界面，包含：
- 会话持久化和沙箱复用
- 斜杠命令（`/help`、`/files`、`/view`、`/download`）
- 使用 `!command` 执行 Bash 命令
- 使用 `@path/to/file` 提及文件
- 可自定义主题和配色方案

快速开始：

```bash
ptc-agent                    # 启动交互式会话
ptc-agent --plan-mode        # 在执行前启用计划审批
ptc-agent list               # 列出可用的 Agent
```

有关完整的 CLI 文档，包括所有选项、命令、键盘快捷键和主题配置，请参阅 **[CLI 参考](../../libs/ptc-cli/README.md)**。

## 路线图

计划中的功能和改进：

- [x] PTC Agent CLI 版本
- [x] Agent Skills 支持（[agentskills.io](https://agentskills.io) 开放标准）
- [x] 用于自动化测试的 CI/CD 流水线
- [ ] 更多 MCP 服务器集成 / 更多示例 Notebook
- [ ] 性能基准测试和优化
- [ ] 改进搜索工具以实现更高效的工具发现

## 贡献

我们欢迎社区贡献！以下是一些您可以提供帮助的方式：

- **代码贡献** - Bug 修复、新功能、改进（CI/CD 即将推出）
- **使用案例** - 分享您在生产或研究中使用 PTC 的方式
- **示例 Notebook** - 创建展示不同工作流程的演示
- **MCP 服务器** - 构建或推荐与 PTC 配合良好的 MCP 服务器（数据处理、API 等）
- **提示技巧** - 分享提高 Agent 性能的提示词

在 [GitHub](https://github.com/Chen-zexi/open-ptc-agent) 上提交 issue 或 PR 来贡献！

## 致谢

本项目基于以下研究和工具构建：

**研究/文章**

- [Introducing advanced tool use on the Claude Developer Platform](https://www.anthropic.com/engineering/advanced-tool-use) - Anthropic
- [Code execution with MCP: building more efficient AI agents](https://www.anthropic.com/engineering/code-execution-with-mcp) - Anthropic
- [CodeAct: Executable Code Actions Elicit Better LLM Agents](https://arxiv.org/abs/2402.01030) - Wang et al.

**框架和基础设施**

- [LangChain DeepAgents](https://github.com/langchain-ai/deepagents) - 基础 Agent 框架
- [Daytona](https://www.daytona.io/) - 沙箱基础设施

## Star 历史

如果您觉得这个项目有用，请考虑给它一个 star！这有助于其他人发现这项工作。

[![Star History Chart](https://api.star-history.com/svg?repos=Chen-zexi/open-ptc-agent&type=Date)](https://star-history.com/#Chen-zexi/open-ptc-agent&Date)

## 许可证

MIT 许可证
