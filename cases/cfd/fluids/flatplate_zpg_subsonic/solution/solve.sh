#!/usr/bin/env bash
# Oracle entry point for flatplate_zpg_subsonic.
#
# This script proves the submitted native case can run from zero through the
# same Allrun contract used by the evaluator.
set -euo pipefail

HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
CASE_RUN="$WORK/submission"

# Copy the reference OpenFOAM case into the working location.
test ! -e "$CASE_RUN" || { echo "FAIL: stale oracle submission" >&2; exit 1; }
mkdir -p "$CASE_RUN"
cp -r "$HERE/case"/. "$CASE_RUN/"

(cd "$CASE_RUN" && bash ./Allrun)

test -f "$CASE_RUN/constant/polyMesh/points" || { echo "FAIL: no mesh" >&2; exit 1; }
solved=""
for directory in "$CASE_RUN"/[0-9]*; do
    [ -d "$directory" ] || continue
    [ "$(basename "$directory")" != 0 ] || continue
    complete=1
    for field in U p k omega nut; do
        [ -s "$directory/$field" ] || complete=0
    done
    if [ "$complete" = 1 ]; then solved="$directory"; break; fi
done
test -n "$solved" || { echo "FAIL: no complete non-zero solved time" >&2; exit 1; }
