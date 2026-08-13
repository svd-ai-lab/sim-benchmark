# Trial architecture

HWE-bench uses Harbor for task selection, environment setup, agent execution
and verifier execution.

```text
published or local dataset
        |
        v
Harbor starts the task environment
        |
        v
agent reads instruction.md and writes /tmp/agent/submission
        |
        v
Harbor transfers declared artifacts to a separate verifier container
        |
        v
verifier performs a clean rerun, derives KPIs and writes reward.json
```

## Task boundary

The agent receives the task instruction and task-supplied inputs. Ground truth,
the oracle solution and verifier implementation are not part of the agent's
working directory.

Every public task declares `/tmp/agent/submission` as its Harbor artifact. The
verifier receives that directory in a separate container and has no network
access through the task's verifier compose configuration.

## Evaluation boundary

The submitted entry point is track-specific:

| track | entry point | reproduced output |
|---|---|---|
| combustion | `python run_case.py` | raw trace CSV and requested artifacts |
| battery | `python run_case.py` | raw trace CSV and requested artifacts |
| CFD | `bash Allrun` | OpenFOAM case, solver output and task CSV interface |

The verifier copies source inputs into a temporary directory, removes generated
numeric artifacts and reruns the entry point under the task's reproduction
budget. KPI values are derived from that reproduced output, not accepted from a
standalone numeric claim.

## Track evaluators

- `native_cantera.py` reproduces the Cantera driver, validates the declared
  initial state, and derives combustion KPIs from the trace.
- `native_pybamm.py` reproduces the PyBaMM driver, validates the cell state and
  trace, and derives battery KPIs.
- `openfoam_interface.py` reproduces `Allrun`, requires serialized mesh and
  solution evidence, and derives KPIs from the declared CSV interface.

All tracks write a scalar `reward.json` for Harbor and a diagnostic
`reward_detail.json`. The normative task and scoring fields are documented in
[`../SCHEMA.md`](../SCHEMA.md).

## Reproducibility coordinate

A run is identified by the dataset/task version, domain image contents, agent,
model, inference settings and resource budget. Published comparisons must keep
those coordinates with the result; a mutable image tag alone is not sufficient.

The solver versions used to calibrate the current tasks are listed in
[`../environment/domains/VERSIONS.md`](../environment/domains/VERSIONS.md) and
recorded per task in `tests/kpis.json`.
