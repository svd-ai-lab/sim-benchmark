#!/usr/bin/env bash
# Oracle for ch4_air_idt_resume_interrupted_run.
#
# The reference answer is the sibling case's, because it is the same solve at
# the same operating point; what this oracle demonstrates is that finishing the
# interrupted run reaches it while keeping the completed rows intact.
set -euo pipefail
HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
SUB="${SIM_BENCH_SUBMISSION:-$WORK/submission}"

mkdir -p "$SUB"
# The interrupted run's files come from the agent's workdir and NOWHERE ELSE.
# Harbor uploads the case's environment/ into the workdir, so if they are
# absent the environment is broken and this must fail loudly rather than reach
# for a copy only the oracle can see -- that asymmetry is what hides a whole
# class of environment faults behind "the models all fail but the oracle passes".
test -d "$WORK/run" || { echo "FAIL: interrupted run missing at $WORK/run" >&2; exit 1; }
cp "$WORK/run/results.csv" "$WORK/run/state.csv" "$SUB/"
cp "$HERE/run_case.py" "$SUB/run_case.py"

cd "$SUB" && python3 run_case.py
