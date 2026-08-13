#!/usr/bin/env bash
set -euo pipefail
HERE="$(dirname "$(realpath "$0")")"
exec python3 "$HERE/verify_native.py"
