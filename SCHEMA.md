# HWE-bench task contract

This document describes the public HWE-bench-CAE tasks in this repository.
Harbor's own task schema remains authoritative for generic `task.toml` fields.

## Task layout

```text
cases/<track>/<subdomain>/<case-id>/
├── task.toml
├── instruction.md
├── environment/              # optional task inputs
├── solution/
│   └── solve.sh              # oracle entry point
└── tests/
    ├── kpis.json             # references and tolerance bands
    ├── spec.json             # output interface and reproduction settings
    ├── test.sh               # Harbor verifier entry point
    └── verify_native.py      # thin track-evaluator adapter
```

All public tasks use Harbor schema `1.3`, declare
`artifacts = ["/tmp/agent/submission"]`, and run the verifier in a separate
container.

## `task.toml`

Required sections and fields are enforced by `tools/lint_case.py`:

- `[task]`: namespaced task name, description, authors and keywords.
- `[environment]`: domain image and task resources.
- `[agent]`: timeout and execution user.
- `[verifier]`: timeout, execution user and separate-container mode.
- `[verifier.environment]`: verifier resources.
- `[metadata.sim]`: solver, provenance classification, difficulty, release and
  oracle status, scoring template, leakage risk and capability target.

The task instruction must not expose ground truth or verifier-only assets.

## Submission contract

Submissions write all deliverables under `/tmp/agent/submission`.

| track | required executable | primary numerical artifact |
|---|---|---|
| combustion | `run_case.py` | `results.csv` raw trace |
| battery | `run_case.py` | `results.csv` raw trace |
| CFD | `Allrun` | task-declared `results.csv` interface plus OpenFOAM output |

The exact columns, units, additional files and runtime budget are part of each
`instruction.md` and `tests/spec.json`.

## Clean reproduction

The verifier does not score the submission's existing numeric artifact in
place. It:

1. copies the submission into an evaluator-owned temporary directory;
2. removes generated output, including the scored CSV;
3. runs the submitted entry point under `reproduction_timeout_s`;
4. reads and derives KPIs from the newly generated artifacts; and
5. applies track-specific structural and physics checks.

This clean-rerun contract is the common provenance mechanism across the three
public tracks.

## `tests/spec.json`

The spec defines only the evaluator inputs that vary by task. Depending on the
track these include:

- physical operating-point fields required for validation;
- the CSV interface and KPI derivations;
- task-supplied paths that must survive cleanup;
- optional resume, comparison or resolution-study requirements; and
- `reproduction_timeout_s`.

Evaluator logic belongs in the shared track evaluator, not in per-task code.

## `tests/kpis.json`

`tests/kpis.json` records:

- the reference value for each KPI;
- its unit and physical admissibility range;
- its absolute `pass_tol`;
- diagnostic `gross_error_tol`, when used;
- group membership; and
- `oracle_provenance`, including the solver/version and tolerance basis.

`pass_tol` is an absolute tolerance in the KPI's unit. A value passes the band
when:

```text
abs(reproduced_value - gt_value) <= pass_tol
```

There is no partial credit within a single KPI band. `gross_error_tol` is
diagnostic and does not affect the score.

## Score

Each evaluator writes:

```json
{"score": 1.0}
```

to `/logs/verifier/reward.json`, plus a track-specific diagnostic breakdown in
`reward_detail.json`.

Conceptually:

```text
final_score = required_gate_product * aggregate_kpi_accuracy
```

Required gates cover whether the evaluator could reproduce and measure the
submission. The exact gates differ by track because a Cantera trace, a PyBaMM
trace and an OpenFOAM solution have different validity evidence. KPI accuracy
is derived from physical-range checks and binary tolerance-band checks.

The oracle for a task must score exactly `1.0`. A task is not accepted solely
because its own oracle passes: a deliberately invalid submission must fail, and
an independently implemented valid submission must pass.

## Public status

The public dataset contains tasks marked `release_status = "public_runnable"`
and `oracle_status = "available"`. [`CASES.md`](CASES.md) is the catalog of the
published set.

## Validation

Run the structural linter over all public tasks:

```bash
python tools/lint_case.py cases/[!_]*/*/*/
```

Run the shared evaluator tests:

```bash
python -m pytest lib/sim_benchmark_verifier/tests
```

For end-to-end oracle validation, run the relevant local task with Harbor after
building its domain image, or run the published task/dataset from Harbor Hub.
