#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.toolchain/bin:$PATH"
TARGET="${1:-spinel}"
APP="${2:-blog}"
OUTPUT="${3:-out/$TARGET}"
case "$TARGET" in ruby|jruby|spinel) ;; *) echo "Unsupported target" >&2; exit 2;; esac
case "$OUTPUT" in out/*) ;; *) echo "Output must be under out/" >&2; exit 2;; esac
[[ "$OUTPUT" != *..* && "$OUTPUT" != out/ ]] || exit 2
export BENCH_APP_DIR="$PWD/$APP"
mkdir -p reports out
bash scripts/prepare-assets.sh 2>&1 | tee reports/assets.log
export ROUNDHOUSE_ASSETS_DIR="$PWD/.cache/static-assets"
roundhouse --version | tee reports/roundhouse-version.txt
roundhouse check --continue "$APP" 2>&1 | tee reports/analysis.log
# Always generate a clean tree. No survey/stub flags are used for emission.
rm -rf "$OUTPUT"
roundhouse --target "$TARGET" -o "$OUTPUT" "$APP" 2>&1 | tee reports/transpile.log
if [[ "$TARGET" = spinel ]]; then test -f "$OUTPUT/spin.toml"; fi
test -f "$OUTPUT/db/seed.sql"
test -s "$OUTPUT/static/assets/tailwind.css"
test -s "$OUTPUT/static/assets/turbo.min.js"
