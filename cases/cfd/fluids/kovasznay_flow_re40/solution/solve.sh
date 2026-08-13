#!/usr/bin/env bash
# Oracle entry point for kovasznay_flow_re40.
#
# Stages the reference submission and runs its own Allrun -- the same path the
# evaluator takes, so a green oracle here means the contract is satisfiable
# exactly as written rather than through some authoring-only shortcut.
set -euo pipefail

HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
SUBMISSION="$WORK/submission"

mkdir -p "$SUBMISSION"
cp -r "$HERE/case"/. "$SUBMISSION/"

if [ -f /usr/lib/openfoam/openfoam2412/etc/bashrc ]; then
    set +e; set +u
    # shellcheck disable=SC1091
    source /usr/lib/openfoam/openfoam2412/etc/bashrc
    set -e; set -u
fi

bash "$SUBMISSION/Allrun"

# The one artifact assertion worth making here: the contract's interface file.
# Everything else the evaluator checks by re-running, and asserting on the case
# internals is what this track is moving away from.
test -f "$SUBMISSION/grid_convergence.csv" \
    || { echo "FAIL: grid_convergence.csv was not written" >&2; exit 1; }
echo "OK: $(wc -l < "$SUBMISSION/grid_convergence.csv") lines in grid_convergence.csv" >&2
