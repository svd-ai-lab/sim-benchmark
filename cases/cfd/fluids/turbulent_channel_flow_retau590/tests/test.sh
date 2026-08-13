#!/usr/bin/env bash
set -euo pipefail
HERE="$(dirname "$(realpath "$0")")"
if [ -f /usr/lib/openfoam/openfoam2412/etc/bashrc ]; then
    # shellcheck disable=SC1091
    set +e; set +u
    source /usr/lib/openfoam/openfoam2412/etc/bashrc
    set -e; set -u
fi
command -v blockMesh >/dev/null
exec python3 "$HERE/verify.py"
