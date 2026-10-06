#!/usr/bin/env bash
# Writes INSTALL.md and bootstrap.sh into ~/forge, then tells you what to do next.
set -euo pipefail
FORGE_HOME="${FORGE_HOME:-$HOME/forge}"
cd "$FORGE_HOME"

# ---------------- INSTALL.md ----------------
cat > INSTALL.md <<'MD'
# FORGE

A local AI mesh. Three hubs on your Mac:

- **model-hub** — runs local LLMs (MLX, llama.cpp, bitnet)
- **verify-hub** — SMT solver (z3) for mathematical claims
- **index-hub** — searchable knowledge base with proof caching

Everything runs offline. No cloud. No accounts.

## Requirements

- macOS 14 (Sonoma) or later
- Apple Silicon (M1/M2/M3/M4)
- 30 GB free disk
- 20 minutes

## Install

Open Terminal. Paste:

    git clone https://github.com/YOUR_USER/forge.git ~/forge
    cd ~/forge && bash bootstrap.sh

Replace YOUR_USER after you push the repo.

If you already have ~/forge on disk, skip the clone and run:

    cd ~/forge && bash bootstrap.sh

## First commands

    cd ~/forge && source activate.sh
    forge ask "write a python function to reverse a list"
    forge prove "there is no integer x with x > 10 and x < 5"
    forge index-put note hello "my first memory"
    forge index-search "memory"

## Start / stop

    ~/forge/start.sh    # start the mesh
    ~/forge/stop.sh     # stop the mesh

The mesh does not start automatically. Run start.sh after every reboot.

## Uninstall

    ~/forge/stop.sh
    rm -rf ~/forge ~/.forge

## What this is not

- Not a ChatGPT replacement
- Not a cloud service
- Not multiplayer (yet)
- Not a business (yet)

## Support

File an issue at github.com/YOUR_USER/forge/issues and include the output of:

    ~/forge/doctor.sh
MD

# ---------------- doctor.sh ----------------
cat > doctor.sh <<'DOC'
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
DOC
chmod +x doctor.sh

# ---------------- bootstrap.sh ----------------
cat > bootstrap.sh <<'BOOT'
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
BOOT
chmod +x bootstrap.sh

# ---------------- done ----------------
printf '\n\033[32m===================================================\033[0m\n'
printf '\033[32m installer pack written to %s\033[0m\n' "$FORGE_HOME"
printf '\033[32m===================================================\033[0m\n'
echo
echo "  files created:"
echo "    $FORGE_HOME/INSTALL.md"
echo "    $FORGE_HOME/bootstrap.sh    (executable)"
echo "    $FORGE_HOME/doctor.sh       (executable)"
echo
echo "  next steps:"
echo
echo "    1. push to GitHub (first time only):"
echo "         cd $FORGE_HOME"
echo "         git init"
echo "         git add -A"
echo "         git commit -m 'forge: initial public bootstrap'"
echo "         gh repo create forge --public --source=. --push   # or use web UI"
echo
echo "    2. edit INSTALL.md and replace YOUR_USER with your GitHub username"
echo "         sed -i '' 's/YOUR_USER/<your-gh-user>/g' $FORGE_HOME/INSTALL.md"
echo
echo "    3. test on a clean macOS user account:"
echo "         - System Settings → Users & Groups → add a Standard user"
echo "         - log out, log in as that user"
echo "         - open Terminal"
echo "         - git clone https://github.com/<your-gh-user>/forge.git ~/forge"
echo "         - cd ~/forge && bash bootstrap.sh"
echo
echo "    4. fix everything that breaks. repeat step 3 until clean."
echo
