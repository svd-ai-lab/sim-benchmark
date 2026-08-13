# Domain-image versions

The repository commit and built image digest are the reproducibility
coordinates. This file summarizes the primary software pinned by the current
Dockerfiles; the Dockerfiles and `tools.sh` files are authoritative.

| image | base | primary solver | numerical and plotting stack |
|---|---|---|---|
| `sim-benchmark-combustion-fullstack:latest` | `python:3.12-slim-bookworm` pinned by digest | Cantera 3.2.0 | NumPy 2.5.1, SciPy 1.18.0, Matplotlib 3.11.1 |
| `sim-benchmark-battery-fullstack:latest` | `python:3.12-slim-bookworm` pinned by digest | PyBaMM 26.7.1.0 | NumPy 2.5.1, SciPy 1.17.1, pandas 3.0.5, Matplotlib 3.11.1 |
| `sim-benchmark-cfd-fullstack:latest` | `opencfd/openfoam-default:2412` pinned by digest | OpenFOAM ESI v2412 | Gmsh, meshio 5.3.5, PyVista 0.48.2, NumPy 2.5.0, SciPy 1.18.0, Matplotlib 3.11.0 |

The shared layer also installs the HWE-bench verifier and the agent CLIs used
by Harbor adapters.

Each task's `tests/kpis.json` records the solver version used for its oracle
reference. A version change requires an oracle sweep and an explicit decision
on whether any reference or tolerance must change.
