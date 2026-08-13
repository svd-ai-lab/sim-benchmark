#!/usr/bin/env bash
# Oracle entry point for naca0012_subsonic.
#
# Stages the reference submission -- which must include the supplied mesh, since
# the contract requires a submission the evaluator can re-run from a clean copy
# -- and runs its own Allrun.
set -euo pipefail
HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
SUBMISSION="${SIM_BENCH_SUBMISSION:-$WORK/submission}"

mkdir -p "$SUBMISSION/constant"
cp -r "$HERE/case"/. "$SUBMISSION/"
test -d "$WORK/constant/polyMesh" || { echo "FAIL: supplied mesh missing at $WORK/constant/polyMesh" >&2; exit 1; }
cp -r "$WORK/constant/polyMesh" "$SUBMISSION/constant/"
bash "$SUBMISSION/Allrun"

test -f "$SUBMISSION/results.csv" \
    || { echo "FAIL: results.csv was not written" >&2; exit 1; }
echo "OK: $(wc -l < "$SUBMISSION/results.csv") lines in results.csv" >&2
