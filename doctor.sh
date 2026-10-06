#!/usr/bin/env bash
set -euo pipefail
ok(){ printf '\033[32m ok %s\033[0m\n' "$*"; }
warn(){ printf '\033[33m !! %s\033[0m\n' "$*"; }
bad(){ printf '\033[31m XX %s\033[0m\n' "$*"; }

echo "== system =="
echo "  macOS $(sw_vers -productVersion)  $(uname -m)"
echo "  free disk: $(df -g "$HOME" | tail -1 | awk '{print $4}')GB"

echo "== binaries =="
for b in brew python3.11 z3 llama-server; do
  if command -v "$b" >/dev/null 2>&1; then ok "$b: $(command -v "$b")"; else bad "$b missing"; fi
done

echo "== venv =="
if [[ -x "$HOME/forge/.venv/bin/python" ]]; then
  ok "python: $("$HOME/forge/.venv/bin/python" --version)"
  for m in zeroconf fastapi uvicorn httpx mlx.core; do
    if "$HOME/forge/.venv/bin/python" -c "import $m" 2>/dev/null; then ok "import $m"; else bad "import $m"; fi
  done
else
  bad "no venv at ~/forge/.venv"
fi

echo "== ports =="
for p in 8080 8090 8095; do
  if lsof -i :"$p" >/dev/null 2>&1; then ok "port $p in use"; else warn "port $p free"; fi
done

echo "== mesh =="
if command -v forge >/dev/null 2>&1; then
  forge hubs 2>/dev/null || warn "forge hubs failed"
else
  bad "forge CLI not on PATH — source ~/forge/activate.sh"
fi

echo "== logs =="
ls -1 "$HOME/.forge/logs" 2>/dev/null || warn "no logs yet"
