<div align="center">

# HWE-bench

**How well can an LLM do hardware engineering?**

<p align="center">
  <a href="https://hwe-bench.svdailab.com/"><img src="https://img.shields.io/badge/Leaderboard-hwe--bench.svdailab.com-3b82f6?style=for-the-badge" alt="Leaderboard"></a>
  <a href="https://hub.harborframework.com/datasets/hwe-bench/hwe-bench/latest"><img src="https://img.shields.io/badge/Dataset-Harbor-009688?style=for-the-badge" alt="HWE-bench on Harbor"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-eab308?style=for-the-badge" alt="License"></a>
</p>

[Tasks](#tasks) · [Evaluation](#evaluation) · [Run](#run) · [Documentation](#documentation)

</div>

---

HWE-bench evaluates LLM agents on hardware-engineering tasks performed with
engineering software. The current **HWE-bench-CAE** edition contains 68 tasks
across combustion, battery and CFD modeling.

## Tasks

Each task specifies a physical system, operating point, requested engineering
output and compute budget. The agent must implement and run a reproducible
workflow that produces the requested result.

| track | tasks | software | examples |
|---|---:|---|---|
| `combustion` | 21 | Cantera | ignition delay, laminar flame speed |
| `battery` | 34 | PyBaMM | discharge, charge, thermal and degradation behavior |
| `cfd` | 13 | OpenFOAM | internal flow, external aerodynamics and natural convection |

See [`CASES.md`](CASES.md) for the complete catalog.

## Evaluation

The verifier copies the submission into a clean directory, removes generated
numeric output, and reruns the submitted entry point. It then derives the task
KPIs from the reproduced artifacts.

For each KPI:

```text
pass = required_checks_pass
       and physics_min <= reproduced_value <= physics_max
       and abs(reproduced_value - reference_value) <= tolerance
```

Required checks depend on the track and include successful clean reproduction,
the declared initial state, required output artifacts, and solver evidence for
CFD tasks. KPI bands are binary; diagnostic details are written separately from
the scalar reward.

References and tolerances are recorded in each task's `tests/kpis.json`. See
[`SCHEMA.md`](SCHEMA.md) for the task contract and [`ORACLE.md`](ORACLE.md) for
the oracle acceptance checks.

## Run

Install [Harbor](https://www.harborframework.com/docs/getting-started), then run
the published dataset:

```bash
uv tool install harbor
harbor run -d hwe-bench/hwe-bench -a oracle
```

Replace `oracle` with a Harbor-supported agent and provide its model and
credentials to evaluate an LLM. Harbor accepts an omitted dataset tag as
`latest`; pin a published tag when comparing runs over time.

For local development:

```bash
git clone https://github.com/svd-ai-lab/hwe-bench
cd hwe-bench
environment/domains/build.sh combustion --intl
harbor run -p cases/combustion/kinetics \
  -i ch4_air_idt_phi0p55_1633k_9p2atm -a oracle
```

See [`REPRODUCING.md`](REPRODUCING.md) for environment and reproducibility
details.

## Repository layout

```text
hwe-bench/
├── cases/<track>/<subdomain>/<case-id>/
│   ├── task.toml
│   ├── instruction.md
│   ├── solution/
│   └── tests/
├── environment/domains/       # battery, combustion and CFD images
├── lib/sim_benchmark_verifier/ # evaluator implementation
├── tools/                      # linting, aggregation and maintenance
└── dataset.toml                # published Harbor dataset manifest
```

`sim-benchmark-*` image names and the `sim_benchmark_verifier` Python package
name are retained technical identifiers.

## Documentation

- [`CASES.md`](CASES.md) — public task catalog
- [`SCHEMA.md`](SCHEMA.md) — authoring and scoring contract
- [`ORACLE.md`](ORACLE.md) — reference-run acceptance
- [`REPRODUCING.md`](REPRODUCING.md) — published and local execution
- [`LEADERBOARD.md`](LEADERBOARD.md) — comparison and reporting rules
- [`docs/architecture.md`](docs/architecture.md) — trial and verifier flow

## Contributing

A task change must pass the schema linter and verifier tests. A new or changed
task also needs evidence that:

1. its oracle scores 1.0;
2. a deliberately invalid submission fails; and
3. an independently implemented correct submission passes.

## Citation

```bibtex
@misc{hwebench2026,
  title  = {HWE-bench: A Hardware-Engineering Agent Benchmark},
  author = {{SVD AI Lab}},
  year   = {2026},
  url    = {https://github.com/svd-ai-lab/hwe-bench}
}
```

## License

Apache 2.0. See [`LICENSE`](LICENSE). Third-party mechanisms, parameter sets,
meshes and reference data retain their upstream licenses.
