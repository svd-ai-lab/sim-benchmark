#!/usr/bin/env bash
# Oracle entry point for channel_developing_entry.
#
# This script proves the case is solvable with the OpenFOAM toolchain in the
# CFD domain image.
set -euo pipefail

HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
CASE_RUN="$WORK/submission"

# Copy the reference OpenFOAM case into the working location.
mkdir -p "$CASE_RUN"
cp -r "$HERE/case"/. "$CASE_RUN/"

export ORACLE_CASE="$CASE_RUN"

# solve_inner.py shells out to blockMesh and simpleFoam, then samples the
# centerline velocity and writes the submission artifacts.
exec python3 "$HERE/solve_inner.py"
