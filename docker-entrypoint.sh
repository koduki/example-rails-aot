#!/bin/sh
set -e

mkdir -p /app/storage

# Initialize database if not already present
if [ ! -f /app/storage/development.sqlite3 ] && [ -f /app/db/seed.sql ]; then
  echo "[ENTRYPOINT] Initializing SQLite database from db/seed.sql..."
  sqlite3 /app/storage/development.sqlite3 < /app/db/seed.sql
fi

# Hand off to the compiled binary as PID 1 to properly handle termination signals
exec /app/blog "$@"
