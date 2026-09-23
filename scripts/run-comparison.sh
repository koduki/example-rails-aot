#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

REPORT_DIR="$ROOT/reports/differential"
mkdir -p "$REPORT_DIR"

RUN_DIR="$(mktemp -d)"
RAILS_PID=''
AOT_PID=''

cleanup() {
  echo "[DIFF] Cleaning up processes and temporary files..."
  if test -n "$RAILS_PID"; then
    kill "$RAILS_PID" 2>/dev/null || true
    wait "$RAILS_PID" 2>/dev/null || true
  fi
  if test -n "$AOT_PID"; then
    kill "$AOT_PID" 2>/dev/null || true
    wait "$AOT_PID" 2>/dev/null || true
  fi
  rm -rf "$RUN_DIR"
}
trap cleanup EXIT

echo "[DIFF] Setting up independent databases..."
mkdir -p "$RUN_DIR/rails_storage"
mkdir -p "$RUN_DIR/aot_run/storage"

# Prepare runtime packages
if ! test -f "$ROOT/artifacts/blog-linux-x86_64.tar.gz"; then
  echo "[DIFF] Packaging native runtime..."
  bash scripts/build.sh
fi

tar -xzf "$ROOT/artifacts/blog-linux-x86_64.tar.gz" -C "$RUN_DIR/aot_run"

# Seed both databases identically using seed.sql
sqlite3 "$RUN_DIR/rails_storage/development.sqlite3" < "$RUN_DIR/aot_run/db/seed.sql"
sqlite3 "$RUN_DIR/aot_run/storage/development.sqlite3" < "$RUN_DIR/aot_run/db/seed.sql"

RAILS_PORT=33000
AOT_PORT=38000

echo "[DIFF] Starting Rails server on port $RAILS_PORT..."
(
  cd "$ROOT/blog"
  export PORT=$RAILS_PORT
  export RAILS_ENV=development
  export DATABASE_URL="sqlite3:$RUN_DIR/rails_storage/development.sqlite3"
  exec bin/rails server -p $RAILS_PORT -b 127.0.0.1
) > "$REPORT_DIR/rails-server.log" 2>&1 &
RAILS_PID=$!

echo "[DIFF] Starting Spinel AOT server on port $AOT_PORT..."
(
  cd "$RUN_DIR/aot_run"
  exec env PORT=$AOT_PORT SPINEL_WORKERS=2 ./blog
) > "$REPORT_DIR/aot-server.log" 2>&1 &
AOT_PID=$!

echo "[DIFF] Waiting for servers to be healthy..."
wait_healthy() {
  local url="$1"
  local pid="$2"
  local log="$3"
  for _ in $(seq 1 60); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[ERROR] Process died unexpectedly. Logs:"
      cat "$log"
      return 1
    fi
    if curl --fail --silent "$url/articles" > /dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "[ERROR] Timeout waiting for $url. Logs:"
  cat "$log"
  return 1
}

wait_healthy "http://127.0.0.1:$RAILS_PORT" "$RAILS_PID" "$REPORT_DIR/rails-server.log"
wait_healthy "http://127.0.0.1:$AOT_PORT" "$AOT_PID" "$REPORT_DIR/aot-server.log"

echo "[DIFF] Running differential comparison suite..."
python3 "$ROOT/scripts/compare.py" \
  "http://127.0.0.1:$RAILS_PORT" \
  "http://127.0.0.1:$AOT_PORT" \
  "$RUN_DIR/rails_storage/development.sqlite3" \
  "$RUN_DIR/aot_run/storage/development.sqlite3" \
  --report-dir "$REPORT_DIR"

echo "[DIFF] Differential comparison succeeded!"
