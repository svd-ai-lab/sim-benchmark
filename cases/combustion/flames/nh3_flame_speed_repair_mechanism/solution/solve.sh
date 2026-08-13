#!/usr/bin/env bash
# Oracle for nh3_flame_speed_repair_mechanism.
#
# The repair is one line: the supplied script runs GRI-Mech 3.0, which carries
# NH3 and a full nitrogen sub-mechanism and therefore executes cleanly, but was
# optimised for natural-gas combustion and NOx formation -- not for ammonia as
# a fuel. Swapping in the ammonia mechanism that ships with Cantera is the whole
# fix; the operating point, domain and refinement are already correct.
set -euo pipefail
HERE="$(dirname "$(realpath "$0")")"
WORK="${AGENT_WORKDIR:-/tmp/agent}"
SUB="${SIM_BENCH_SUBMISSION:-$WORK/submission}"
mkdir -p "$SUB"

# The starting fixtures come from the agent's workdir and NOWHERE ELSE. They are
# staged by the runner (Harbor uploads the case's environment/ into the workdir),
# so if they are absent the environment is broken and this must fail loudly. The
# old form fell back to a copy reachable only from solution/, which let the oracle
# succeed down a path the agent does not have -- exactly the asymmetry that hid a
# whole class of environment faults behind "the models all fail but the oracle
# passes".
test -f "$WORK/run_case.py" || { echo "FAIL: starting script missing at $WORK/run_case.py" >&2; exit 1; }
cp "$WORK/run_case.py" "$SUB/run_case.py"

python3 - "$SUB/run_case.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
assert "MECHANISM = 'gri30.yaml'" in s, "harness no longer carries the injected defect"
s = s.replace("MECHANISM = 'gri30.yaml'",
              "MECHANISM = 'example_data/ammonia-CO-H2-Alzueta-2023.yaml'")
open(p, "w").write(s)
PY

cd "$SUB"
python3 run_case.py
