#!/usr/bin/env bash
# Oracle entry point for turbulent_channel_flow_retau590.
#
# This script proves the case is solvable with the toolchain in
# sim-benchmark-cfd-fullstack. It invokes OpenFOAM natively and
# leaves the same native case layout required from an agent submission.
#
# solve_inner.py shells out to blockMesh + simpleFoam (k-omega SST, periodic
# channel driven by a uniform body force so u_tau = 1) + postProcess, samples
# the wall-normal velocity profile, integrates it to the bulk-mean velocity
# U_b, and forms U_b/u_tau as an author diagnostic. The evaluator later derives
# the KPI independently from the persisted velocity field.
set -euo pipefail

HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
CASE_RUN="$WORK/submission"

# Copy the reference OpenFOAM case into the working location.
if [ -e "$CASE_RUN" ]; then
    echo "FAIL: oracle submission path already exists: $CASE_RUN" >&2
    echo "Use a fresh AGENT_WORKDIR so stale fields cannot satisfy assertions." >&2
    exit 1
fi
mkdir -p "$CASE_RUN"
cp -r "$HERE/case"/. "$CASE_RUN/"

# Exercise the same clean native workflow required from an agent.
(cd "$CASE_RUN" && bash ./Allrun)

# ---- native submission assertions ------------------------------------------
# 1. a real mesh exists (polyMesh/ with points + faces).
test -f "$CASE_RUN/constant/polyMesh/points" \
    || { echo "FAIL: constant/polyMesh/points missing (no real mesh)" >&2; exit 1; }
test -f "$CASE_RUN/constant/polyMesh/faces" \
    || { echo "FAIL: constant/polyMesh/faces missing (no real mesh)" >&2; exit 1; }

# 2. at least one NON-ZERO time directory with solved U and p fields exists.
nonzero_time_with_fields=""
for d in "$CASE_RUN"/[0-9]*; do
    [ -d "$d" ] || continue
    t="$(basename "$d")"
    # skip the "0" initial-condition directory
    if [ "$t" = "0" ]; then continue; fi
    if [ -s "$d/U" ] && [ -s "$d/p" ] && [ -s "$d/k" ] \
       && [ -s "$d/omega" ] && [ -s "$d/nut" ]; then
        nonzero_time_with_fields="$d"
        break
    fi
done
test -n "$nonzero_time_with_fields" \
    || { echo "FAIL: no non-zero time dir with U and p (solver did not run)" >&2; exit 1; }

test -f "$CASE_RUN/Allrun" \
    || { echo "FAIL: Allrun missing" >&2; exit 1; }

echo "OK: native case + polyMesh + solved time dir ($nonzero_time_with_fields) present" >&2
