#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

REPORT_DIR="$ROOT/reports/differential"
mkdir -p "$REPORT_DIR"

RUN_DIR="$(mktemp -d)"
RAILS_PID=''
AOT_PID=''

ORIG_DB_BACKUP=''
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
  if test -n "$ORIG_DB_BACKUP" && test -f "$ORIG_DB_BACKUP"; then
    mv "$ORIG_DB_BACKUP" "$ROOT/blog/storage/development.sqlite3" 2>/dev/null || true
  fi
  rm -rf "$RUN_DIR"
}
trap cleanup EXIT

echo "[DIFF] Setting up isolated and identical databases..."
mkdir -p "$RUN_DIR/rails_storage"
mkdir -p "$RUN_DIR/aot_run/storage"

# Prepare runtime packages
if ! test -f "$ROOT/artifacts/blog-linux-x86_64.tar.gz"; then
  echo "[DIFF] Packaging native runtime..."
  bash scripts/build.sh
fi

tar -xzf "$ROOT/artifacts/blog-linux-x86_64.tar.gz" -C "$RUN_DIR/aot_run"

# Backup existing development DB if present
if test -f "$ROOT/blog/storage/development.sqlite3"; then
  ORIG_DB_BACKUP="$RUN_DIR/orig_development.sqlite3"
  mv "$ROOT/blog/storage/development.sqlite3" "$ORIG_DB_BACKUP"
fi

# Run db:prepare to build complete schema, migrations table, and seed data
(
  cd "$ROOT/blog"
  bundle exec rails db:prepare
)

# Populate isolated databases with the exact same initial state
cp "$ROOT/blog/storage/development.sqlite3" "$RUN_DIR/rails_storage/development.sqlite3"
cp "$ROOT/blog/storage/development.sqlite3" "$RUN_DIR/aot_run/storage/development.sqlite3"

# Link Rails development DB to the isolated test database
rm -f "$ROOT/blog/storage/development.sqlite3"
ln -s "$RUN_DIR/rails_storage/development.sqlite3" "$ROOT/blog/storage/development.sqlite3"

RAILS_PORT=33000
AOT_PORT=38000

echo "[DIFF] Starting Rails server on port $RAILS_PORT..."
(
  cd "$ROOT/blog"
  export PORT=$RAILS_PORT
  export RAILS_ENV=development
  exec bundle exec rails server -p $RAILS_PORT -b 127.0.0.1
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
