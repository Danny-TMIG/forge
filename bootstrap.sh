#!/usr/bin/env bash
set -euo pipefail

FORGE_HOME="${FORGE_HOME:-$HOME/forge}"
PY="python3.11"
MIN_FREE_GB=30

bold(){ printf '\n\033[1m%s\033[0m\n' "$*"; }
note(){ printf '\033[36m==> %s\033[0m\n' "$*"; }
ok(){   printf '\033[32m ok %s\033[0m\n' "$*"; }
warn(){ printf '\033[33m !! %s\033[0m\n' "$*"; }
die(){  printf '\033[31m XX %s\033[0m\n' "$*" >&2; exit 1; }

bold "FORGE bootstrap"

# 1. sanity
[[ "$(uname)" == "Darwin" ]] || die "macOS only"
[[ "$(uname -m)" == "arm64" ]] || die "Apple Silicon required"
note "macOS $(sw_vers -productVersion) on arm64"
FREE=$(df -g "$HOME" | tail -1 | awk '{print $4}')
(( FREE >= MIN_FREE_GB )) || die "need ${MIN_FREE_GB}GB free, have ${FREE}GB"
ok "disk ${FREE}GB free"

# 2. homebrew
if ! command -v brew >/dev/null 2>&1; then
  note "installing homebrew"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true
ok "homebrew"

# 3. python
command -v "$PY" >/dev/null 2>&1 || { note "installing $PY"; brew install python@3.11; }
ok "$PY $($PY --version)"

# 4. z3 + llama.cpp
command -v z3 >/dev/null 2>&1 || { note "installing z3"; brew install z3; }
ok "z3 $(z3 --version | head -1)"
command -v llama-server >/dev/null 2>&1 || { note "installing llama.cpp"; brew install llama.cpp; }
ok "llama.cpp"

# 5. venv + deps
cd "$FORGE_HOME"
[[ -d .venv ]] || { note "creating venv"; "$PY" -m venv .venv; }
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel >/dev/null
note "installing forge package"
pip install -e . >/dev/null
note "installing mlx-lm"
pip install mlx-lm >/dev/null
ok "deps"

# 6. activate helper
cat > activate.sh <<'ENV'
cd "$(dirname "${BASH_SOURCE[0]}")"
source .venv/bin/activate
export FORGE_MLX_MODEL="${FORGE_MLX_MODEL:-mlx-community/Qwen2.5-Coder-7B-Instruct-4bit}"
export PATH="$PWD/.venv/bin:$PATH"
echo "forge environment active"
ENV
chmod +x activate.sh

# 7. start
note "starting mesh"
"$FORGE_HOME/start.sh" || die "start.sh failed — see ~/.forge/logs/"

# 8. smoke
note "smoke test (first call downloads the model, up to 5 min)"
if forge ask "say hello in three words" 2>&1 | tee /tmp/forge-smoke.txt | grep -qiE 'hello|hi|hey|greet'; then
  ok "model responds"
else
  warn "model did not answer — check ~/.forge/logs/model-hub.log"
fi

# 9. summary
bold "==================================================="
bold " forge is ready"
bold "==================================================="
echo
echo "  cd $FORGE_HOME && source activate.sh"
echo "  forge ask \"hello\""
echo "  forge prove \"there is no integer x with x > 10 and x < 5\""
echo
echo "  stop the mesh:  $FORGE_HOME/stop.sh"
echo "  diagnostics:    $FORGE_HOME/doctor.sh"
echo "  logs:           ~/.forge/logs/"
echo
