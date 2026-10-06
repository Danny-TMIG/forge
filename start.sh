#!/usr/bin/env bash
set -euo pipefail

FORGE_HOME="${FORGE_HOME:-$HOME/forge}"
VENV_PY="$FORGE_HOME/.venv/bin/python"
LOGDIR="$HOME/.forge/logs"
PIDFILE="$HOME/.forge/pids"
mkdir -p "$LOGDIR"

ok(){   printf '\033[32m ok %s\033[0m\n' "$*"; }
note(){ printf '\033[36m==> %s\033[0m\n' "$*"; }
die(){  printf '\033[31m XX %s\033[0m\n' "$*" >&2; exit 1; }

[[ -x "$VENV_PY" ]] || die "no venv python at $VENV_PY"
"$VENV_PY" -c "import zeroconf,fastapi,uvicorn,httpx" 2>/dev/null \
  || die "venv missing deps — run: cd $FORGE_HOME && source .venv/bin/activate && pip install -e ."

note "killing prior forge processes"
pkill -9 -f 'forge\.hubs' 2>/dev/null || true
pkill -9 -f 'mlx_lm.server' 2>/dev/null || true
sleep 1

note "freeing ports 8080 8081 8090 8095"
for p in 8080 8081 8090 8095; do
  pids=$(lsof -ti :"$p" 2>/dev/null || true)
  [[ -n "$pids" ]] && echo "$pids" | xargs -r kill -9
done
sleep 1

still=$(lsof -i :8080 -i :8090 -i :8095 2>/dev/null || true)
[[ -n "$still" ]] && { echo "$still"; die "ports still held"; }
ok "ports free"

note "starting hubs"
nohup "$VENV_PY" -m forge.hubs.model  > "$LOGDIR/model-hub.log"  2>&1 &
MPID=$!
nohup "$VENV_PY" -m forge.hubs.verify > "$LOGDIR/verify-hub.log" 2>&1 &
VPID=$!
nohup "$VENV_PY" -m forge.hubs.index  > "$LOGDIR/index-hub.log"  2>&1 &
IPID=$!

printf '%s\n%s\n%s\n' "$MPID" "$VPID" "$IPID" > "$PIDFILE"

# wait on port, checking process is alive
wait_for(){
  local name=$1 port=$2 pid=$3 path=$4 want=$5 budget=$6
  local end=$(( $(date +%s) + budget ))
  while (( $(date +%s) < end )); do
    kill -0 "$pid" 2>/dev/null || { tail -25 "$LOGDIR/$name.log"; die "$name exited"; }
    local body
    body=$(curl -sf -m 2 "http://127.0.0.1:$port$path" 2>/dev/null || true)
    if [[ "$body" == *"$want"* ]]; then ok "$name on $port"; return 0; fi
    sleep 1
  done
  tail -25 "$LOGDIR/$name.log"
  die "$name not responding on $port$path after ${budget}s"
}

wait_for model-hub  8080 "$MPID" /v1/models '"object":"list"' 300
wait_for verify-hub 8090 "$VPID" /health    '"ok":true'        30
wait_for index-hub  8095 "$IPID" /health    '"ok":true'        30

echo
ok "mesh up — pids in $PIDFILE"
echo "  model-hub   $MPID"
echo "  verify-hub  $VPID"
echo "  index-hub   $IPID"
echo "  stop with:  $FORGE_HOME/stop.sh"
