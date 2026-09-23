#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.toolchain/bin:$PATH"
mkdir -p reports out
bash scripts/prepare-assets.sh 2>&1 | tee reports/assets.log
export ROUNDHOUSE_ASSETS_DIR="$PWD/.cache/static-assets"
roundhouse --version | tee reports/roundhouse-version.txt
roundhouse check --continue blog 2>&1 | tee reports/analysis.log
# Always generate a clean tree. No survey/stub flags are used for emission.
rm -rf out/spinel
roundhouse --target spinel -o out/spinel blog 2>&1 | tee reports/transpile.log
test -f out/spinel/spin.toml
test -f out/spinel/db/seed.sql
test -s out/spinel/static/assets/tailwind.css
test -s out/spinel/static/assets/turbo.min.js
