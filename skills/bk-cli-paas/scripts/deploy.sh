#!/usr/bin/env bash
# Thin wrapper kept for compatibility; the deployment workflow lives in deploy.py.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/deploy.py" "$@"
