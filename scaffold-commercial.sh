#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

YEAR=$(date +%Y)
OWNER="${FORGE_OWNER:-Danny-TMIG}"

mkdir -p .github/ISSUE_TEMPLATE .github/workflows docs examples tests

# ... paste the rest of your script body here ...
