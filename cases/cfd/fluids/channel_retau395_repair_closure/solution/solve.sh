#!/usr/bin/env bash
# Oracle for channel_retau395_repair_closure.
#
# The repair is a single modelling decision: the supplied case runs a laminar
# closure on a Re_tau = 395 channel, which is why its bulk velocity comes out
# near the closed-form Poiseuille value instead of the turbulent one. Restore
# the RANS closure the mesh was built for and re-run; nothing else changes.
set -euo pipefail
HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
SUB="${SIM_BENCH_SUBMISSION:-$WORK/submission}"

mkdir -p "$SUB"
# Take the case the agent is given, from the working dir if the runner staged
# it there, otherwise from the case's own harness copy.
# The starting fixtures come from the agent's workdir and NOWHERE ELSE. They are
# staged by the runner (Harbor uploads the case's environment/ into the workdir),
# so if they are absent the environment is broken and this must fail loudly. The
# old form fell back to a copy reachable only from solution/, which let the oracle
# succeed down a path the agent does not have -- exactly the asymmetry that hid a
# whole class of environment faults behind "the models all fail but the oracle
# passes".
test -d "$WORK/case" || { echo "FAIL: starting case missing at $WORK/case" >&2; exit 1; }
cp -r "$WORK/case/." "$SUB/"

cat > "$SUB/constant/turbulenceProperties" <<'FOAM'
/*--------------------------------*- C++ -*----------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      turbulenceProperties;
}

simulationType  RAS;

RAS
{
    RASModel        kOmegaSST;
    turbulence      on;
    printCoeffs     on;
}
FOAM

chmod +x "$SUB/Allrun"
cd "$SUB" && ./Allrun
