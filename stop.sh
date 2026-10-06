#!/usr/bin/env bash
set -euo pipefail
PIDFILE="$HOME/.forge/pids"
if [[ -f "$PIDFILE" ]]; then
  while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDFILE"
  rm -f "$PIDFILE"
fi
pkill -f 'forge\.hubs' 2>/dev/null || true
pkill -f 'mlx_lm.server' 2>/dev/null || true
sleep 1
for p in 8080 8081 8090 8095; do
  pids=$(lsof -ti :"$p" 2>/dev/null || true)
  [[ -n "$pids" ]] && echo "$pids" | xargs -r kill -9
done
echo "forge stopped"
