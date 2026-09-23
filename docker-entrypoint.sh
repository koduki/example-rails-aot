#!/bin/sh
set -e

# If the first argument is not an option (does not start with -) and is not empty,
# and it's not the default binary execution, execute the passed command directly
if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ] && [ "$1" != "/app/blog" ]; then
  exec "$@"
fi

mkdir -p /app/storage

# Initialize database if not already present
if [ ! -f /app/storage/development.sqlite3 ] && [ -f /app/db/seed.sql ]; then
  echo "[ENTRYPOINT] Initializing SQLite database from db/seed.sql..."
  sqlite3 /app/storage/development.sqlite3 < /app/db/seed.sql
fi

# Hand off to the compiled binary as PID 1 to properly handle termination signals
exec /app/blog "$@"
