#!/usr/bin/env bash
# Fires once per session, right before the first Edit/Write/MultiEdit, to put
# AGENTS.md's sync/branch ritual in front of the agent regardless of whether
# the session started in Plan Mode or with a direct prompt.
set -euo pipefail

input=$(cat)
session_id=$(printf '%s' "$input" | jq -r '.session_id // "unknown"')
marker="${TMPDIR:-/tmp}/claude-agents-md-ritual-${session_id}"

if [ -f "$marker" ]; then
  exit 0
fi
touch "$marker"

branch=$(git branch --show-current 2>/dev/null || echo "?")

reminder="Antes del primer cambio de código de esta sesión (branch actual: '${branch}'), corré el ritual de AGENTS.md §1: (1) \`git status\` y \`git branch --show-current\` — si hay cambios sin commitear que no son tuyos de esta sesión, preguntá antes de tocarlos; (2) \`git fetch origin main:main\` para sincronizar main local; (3) chequeá si '${branch}' ya está mergeada a main (\`git log main..${branch} --oneline\` vacío = mergeada) — si está mergeada, si es 'main', o si es de un pedido distinto al actual, creá una branch nueva desde el main recién sincronizado antes de seguir editando."

jq -n --arg ctx "$reminder" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "allow",
    additionalContext: $ctx
  }
}'
