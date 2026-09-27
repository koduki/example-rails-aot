#!/usr/bin/env bash
# Shell wrapper to run benchmark tests or CLI inside a Linux container.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMAND="${1:-tests}"
shift || true

if [ "$COMMAND" = "tests" ]; then
    echo "Running tests inside Linux container (python:3.12-slim)..."
    docker run --rm \
        -v "${REPO_ROOT}:/workspace" \
        -w /workspace \
        python:3.12-slim \
        python3 -m unittest discover -s tests/bench -v
else
    echo "Running benchmark command inside Linux container: $COMMAND $*"
    docker run --rm \
        -v "${REPO_ROOT}:/workspace" \
        -v "/var/run/docker.sock:/var/run/docker.sock" \
        -w /workspace \
        python:3.12-slim \
        python3 scripts/bench/run.py "$COMMAND" "$@"
fi
