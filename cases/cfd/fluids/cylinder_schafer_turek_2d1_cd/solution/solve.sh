#!/usr/bin/env bash
# Oracle entry point for cylinder_schafer_turek_2d1_cd.
#
# The Python orchestrator runs blockMesh, simpleFoam and the forceCoeffs
# function object, then extracts the drag coefficient. It asserts that the
# expected result and OpenFOAM artifacts exist.
set -euo pipefail

HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
CASE_RUN="$WORK/submission"

mkdir -p "$CASE_RUN"
cp -r "$HERE/case"/. "$CASE_RUN/"

export ORACLE_CASE="$CASE_RUN"

python3 "$HERE/solve_inner.py"

# --- oracle self-checks: real OpenFOAM artifacts + result.json --------------
test -f /tmp/agent/result.json || { echo "MISSING result.json" >&2; exit 1; }
test -d "$CASE_RUN/constant/polyMesh" || { echo "MISSING polyMesh/" >&2; exit 1; }

# at least one non-zero time directory holding U and p
found_time=""
for d in "$CASE_RUN"/[1-9]*; do
    [ -d "$d" ] || continue
    if [ -f "$d/U" ] && [ -f "$d/p" ]; then
        found_time="$d"
        break
    fi
done
test -n "$found_time" || { echo "MISSING non-zero time dir with U,p" >&2; exit 1; }

echo "[solve.sh] artifacts OK: polyMesh + $found_time + result.json"
