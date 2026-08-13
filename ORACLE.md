# Oracle baseline

Every public task includes `solution/solve.sh`, a known-good submission run by
Harbor's `oracle` agent. It is an instrument check for the task, image and
verifier at a specific version.

## Run an oracle

From the published dataset:

```bash
harbor run -d hwe-bench/hwe-bench -a oracle
```

From a local checkout, after building the task's domain image:

```bash
harbor run -p cases/combustion/kinetics \
  -i ch4_air_idt_phi0p55_1633k_9p2atm -a oracle
```

Harbor writes trial artifacts under `jobs/`. Inspect the verifier's
`reward.json` and `reward_detail.json` for the score and its diagnostic
breakdown.

## Acceptance checks

| check | required result |
|---|---|
| task oracle | exactly `1.0` |
| same oracle on the same pinned environment | same reproduced KPI and score |
| deliberately invalid submission | fails the relevant gate or KPI band |
| independently implemented correct submission | `1.0` |

An oracle matching a reference produced by the same solver establishes
reproducibility, not experimental correctness. Each `tests/kpis.json` records
the reference method, solver version and tolerance basis so that distinction is
visible.

If an oracle moves after an environment change, do not widen the tolerance by
default. First determine whether the image, solver, evaluator, task inputs or
reference changed. Recalibration is a reviewed task-contract change.

Related documents: [`SCHEMA.md`](SCHEMA.md),
[`REPRODUCING.md`](REPRODUCING.md), and [`CASES.md`](CASES.md).
