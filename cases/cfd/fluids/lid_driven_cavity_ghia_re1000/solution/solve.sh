#!/usr/bin/env bash
# Oracle entry point for lid_driven_cavity_ghia_re1000.
#
# This script proves the case is solvable with the OpenFOAM toolchain in the
# CFD domain image.
#
# solve_inner.py shells out to blockMesh + simpleFoam (laminar) + postProcess,
# samples the vertical-centerline (x=0.5) velocity, and writes the submission
# artifacts.
set -euo pipefail

HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
CASE_RUN="$WORK/submission"

# Copy the reference OpenFOAM case into the working location.
mkdir -p "$CASE_RUN"
cp -r "$HERE/case"/. "$CASE_RUN/"

export ORACLE_CASE="$CASE_RUN"

python3 "$HERE/solve_inner.py"

# ---- artifact assertions (anti-cheat gate the verifier also enforces) -------
# 1. result.json was written.
test -f "$WORK/result.json" \
    || { echo "FAIL: $WORK/result.json missing" >&2; exit 1; }

# 2. a real mesh exists (polyMesh/ with points + faces).
test -f "$CASE_RUN/constant/polyMesh/points" \
    || { echo "FAIL: constant/polyMesh/points missing (no real mesh)" >&2; exit 1; }
test -f "$CASE_RUN/constant/polyMesh/faces" \
    || { echo "FAIL: constant/polyMesh/faces missing (no real mesh)" >&2; exit 1; }

# 3. at least one NON-ZERO time directory with solved U and p fields exists.
nonzero_time_with_fields=""
for d in "$CASE_RUN"/[0-9]*; do
    [ -d "$d" ] || continue
    t="$(basename "$d")"
    # skip the "0" initial-condition directory
    if [ "$t" = "0" ]; then continue; fi
    if [ -f "$d/U" ] && [ -f "$d/p" ]; then
        nonzero_time_with_fields="$d"
        break
    fi
done
test -n "$nonzero_time_with_fields" \
    || { echo "FAIL: no non-zero time dir with U and p (solver did not run)" >&2; exit 1; }

echo "OK: result.json + polyMesh + solved time dir ($nonzero_time_with_fields) present" >&2
