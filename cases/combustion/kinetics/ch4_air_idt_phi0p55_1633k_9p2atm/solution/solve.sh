#!/usr/bin/env bash
# Oracle: produce a reference submission that scores ~1.0.
set -euo pipefail
HERE="$(dirname "$(realpath "$0")")"
SUB="${SIM_BENCH_SUBMISSION:-/tmp/agent/submission}"
mkdir -p "$SUB"
cp "$HERE/run_case.py" "$SUB/run_case.py"
cd "$SUB"
python3 run_case.py
