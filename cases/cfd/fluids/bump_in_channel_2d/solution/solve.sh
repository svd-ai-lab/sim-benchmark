#!/usr/bin/env bash
# Oracle entry point for bump_in_channel_2d.
#
# Stages the reference submission and runs its own Allrun -- the same path the
# evaluator takes, so a green oracle means the contract is satisfiable exactly
# as written rather than through an authoring-only shortcut.
set -euo pipefail
HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
SUBMISSION="${SIM_BENCH_SUBMISSION:-$WORK/submission}"

mkdir -p "$SUBMISSION/constant"
cp -r "$HERE/case"/. "$SUBMISSION/"
# The published grid is a task input: Harbor uploads the case's environment/ into
# the agent's working directory, and a submission that is to be re-runnable has to
# carry it. tests/spec.json lists it under `preserve` so the evaluator's clean copy
# keeps it rather than treating it as generated state.
test -d "$WORK/constant/polyMesh" || { echo "FAIL: supplied mesh missing at $WORK/constant/polyMesh" >&2; exit 1; }
cp -r "$WORK/constant/polyMesh" "$SUBMISSION/constant/"
bash "$SUBMISSION/Allrun"

test -f "$SUBMISSION/results.csv" \
    || { echo "FAIL: results.csv was not written" >&2; exit 1; }
echo "OK: $(wc -l < "$SUBMISSION/results.csv") lines in results.csv" >&2
