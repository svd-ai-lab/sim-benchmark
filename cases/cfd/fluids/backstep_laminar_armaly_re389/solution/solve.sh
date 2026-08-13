#!/usr/bin/env bash
# Oracle entry point for backstep_laminar_armaly_re389.
#
# The Python orchestrator runs blockMesh, simpleFoam and post-processing, then
# writes the task's submission artifacts.
set -euo pipefail

HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
CASE_RUN="$WORK/submission"

mkdir -p "$CASE_RUN"
cp -r "$HERE/case"/. "$CASE_RUN/"

export ORACLE_CASE="$CASE_RUN"

exec python3 "$HERE/solve_inner.py"
