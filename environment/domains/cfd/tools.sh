#!/usr/bin/env bash
# cfd toolchain — industrial CFD full flow.
#
# OS root is opencfd/openfoam-default:2412 (OpenFOAM is NOT apt-installable,
# so it must come from the image). CORE adds the meshing + post-processing
# + scripting tools a CFD engineer pairs with OpenFOAM all day.
#
# Edit this file to add/remove tools (see environment/domains/README.md).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# apt is pointed at a fast mirror for this script only; see the snippet for why
# a lasting rewrite is a trap. No-op unless APT_MIRROR is set.
. /tmp/apt_mirror.sh

# ===================== CORE (reliable; always built) =====================

apt-get update
apt-get install -y --no-install-recommends \
    curl ca-certificates git \
    python3 python3-pip \
    gmsh
rm -rf /var/lib/apt/lists/*

# --- Python CFD pre/post stack ---------------------------------------------
python3 -m pip install --no-cache-dir --upgrade pip || true
PIP_BSP=""; python3 -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages' && PIP_BSP="--break-system-packages"
# Exact pins, not ranges. A domain image is the environment a ground truth was
# calibrated in, so "whatever pip resolved on the day someone rebuilt" is not a
# reproducible environment -- and the drift is not hypothetical: cfd and
# combustion were built the same day off identically loose specs and landed on
# numpy 2.5.0 and 2.5.1 respectively. Each case's
# tests/kpis.json `oracle_provenance.<solver>_version` is checked against
# environment/domains/VERSIONS.md, and an unpinned install makes that manifest
# quietly false rather than loudly wrong. Bumping one of these is an
# environment-layer change: edit here, rebuild, re-run that domain's oracles,
# update the manifest -- see VERSIONS.md.
python3 -m pip install --no-cache-dir ${PIP_BSP} \
    "meshio[all]==5.3.5" \
    "pyvista==0.48.2" \
    "numpy==2.5.0" \
    "scipy==1.18.0" \
    "matplotlib==3.11.0"

# --- sanity ----------------------------------------------------------------
# OpenFOAM env is sourced via /etc/profile.d in login shells.
gmsh --version 2>&1 | head -1 || true
python3 -c "import meshio, pyvista; print('meshio', meshio.__version__)" || true

# ===================== OPTIONAL (heavy/finicky; opt-in) ==================
# SU2 — compressible/adjoint CFD. No apt pkg; fetch the official binary:
#   SU2_URL="https://github.com/su2code/SU2/releases/download/v8.1.0/SU2-v8.1.0-linux64.zip"
#   curl -fkSL "${SU2_URL}" -o /tmp/su2.zip && unzip /tmp/su2.zip -d /opt/su2 && rm /tmp/su2.zip
#   echo 'export PATH=/opt/su2/bin:$PATH; export SU2_RUN=/opt/su2/bin' > /etc/profile.d/su2.sh
#
# ParaView (headless pvbatch/pvpython) — large-scale viz + IntegrateVariables.
# ~1.5 GB. apt:
#   apt-get update && apt-get install -y --no-install-recommends paraview python3-paraview
#
# Add optional CFD utilities only when a public task requires them.
