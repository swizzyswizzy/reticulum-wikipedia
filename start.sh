#!/bin/bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
export LOUNGE_PORT="${LOUNGE_PORT:-80}"
exec python3 "$ROOT/app.py" "$@"
