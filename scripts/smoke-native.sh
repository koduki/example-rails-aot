#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
mkdir -p reports
RUN_DIR="$(mktemp -d)"
SERVER_PID=''
cleanup() {
  if test -n "$SERVER_PID"; then kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; fi
  rm -rf "$RUN_DIR"
}
trap cleanup EXIT
tar -xzf artifacts/blog-linux-x86_64.tar.gz -C "$RUN_DIR"
mkdir -p "$RUN_DIR/storage"
sqlite3 "$RUN_DIR/storage/development.sqlite3" < "$RUN_DIR/db/seed.sql"
start() {
  (cd "$RUN_DIR"; exec env PORT=38000 SPINEL_WORKERS=2 ./blog) >> "$ROOT/reports/native-server.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 60); do
    kill -0 "$SERVER_PID" || { cat "$ROOT/reports/native-server.log"; return 1; }
    if curl --fail --silent http://127.0.0.1:38000/articles > /dev/null; then return 0; fi
    sleep 1
  done
  cat "$ROOT/reports/native-server.log"
  return 1
}
start
python3 scripts/smoke.py http://127.0.0.1:38000 "$RUN_DIR/storage/development.sqlite3" create | tee reports/native-create.json
kill "$SERVER_PID"
wait "$SERVER_PID" || true
SERVER_PID=''
start
python3 scripts/smoke.py http://127.0.0.1:38000 "$RUN_DIR/storage/development.sqlite3" restart | tee reports/native-restart.json
