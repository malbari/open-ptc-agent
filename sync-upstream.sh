#!/bin/bash
#
# sync-upstream.sh - Sincronizza il fork con il repository originale
#
# Questo script:
# 1. Aggiunge il repository originale come 'upstream' (se non esiste)
# 2. Fa fetch e merge delle modifiche dall'upstream
# 3. Mantiene il fork come 'origin' per le push
#
# Uso:
#   ./sync-upstream.sh [branch]
#
#   branch: branch da sincronizzare (default: main)
#

set -e

# Configurazione
UPSTREAM_URL="https://github.com/Chen-zexi/open-ptc-agent"
UPSTREAM_NAME="upstream"
DEFAULT_BRANCH="main"

# Branch da sincronizzare
BRANCH="${1:-$DEFAULT_BRANCH}"

echo "=== Sincronizzazione fork con repository upstream ==="
echo ""

# Verifica se upstream esiste già
if ! git remote | grep -q "^${UPSTREAM_NAME}$"; then
    echo "➕ Aggiungendo remote 'upstream'..."
    git remote add "$UPSTREAM_NAME" "$UPSTREAM_URL"
    echo "   Remote 'upstream' aggiunto: $UPSTREAM_URL"
else
    echo "✓ Remote 'upstream' già presente"
fi

echo ""
echo "📥 Fetch delle modifiche da upstream..."
git fetch "$UPSTREAM_NAME"

echo ""
echo "🌿 Branch corrente: $(git branch --show-current)"
echo "   Target branch: $BRANCH"

# Se non siamo sul branch target, chiedi se cambiare
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
    echo ""
    read -p "Vuoi passare al branch '$BRANCH'? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        git checkout "$BRANCH"
    else
        echo "Continuo sul branch corrente..."
        BRANCH="$CURRENT_BRANCH"
    fi
fi

echo ""
echo "🔄 Merge delle modifiche da upstream/$BRANCH..."
git merge "$UPSTREAM_NAME/$BRANCH" --no-edit

echo ""
echo "✅ Sincronizzazione completata!"
echo ""
echo "📝 Remote configurati:"
echo "   - origin:   $(git remote get-url origin)"
echo "   - upstream: $(git remote get-url upstream)"
echo ""
echo "💡 Per pushare le modifiche al tuo fork:"
echo "   git push origin $BRANCH"
