# Domain images

HWE-bench-CAE uses one image for each public track.

| image | primary software | tasks |
|---|---|---|
| `sim-benchmark-combustion-fullstack:latest` | Cantera | combustion kinetics and flames |
| `sim-benchmark-battery-fullstack:latest` | PyBaMM | cell, characterization, thermal and degradation tasks |
| `sim-benchmark-cfd-fullstack:latest` | OpenFOAM, Gmsh and Python post-processing | CFD tasks |

The image names retain the repository's earlier technical namespace.

## Build

Run from the repository root:

```bash
environment/domains/build.sh combustion --intl
environment/domains/build.sh battery --intl
environment/domains/build.sh cfd --intl
```

Without `--intl`, the Dockerfiles use CN-oriented package mirrors. Other build
options are shown by running `environment/domains/build.sh` without arguments.

## Image structure

Each image combines:

1. a pinned operating-system or vendor base;
2. the domain's solver and numerical/plotting dependencies from `tools.sh`;
3. the shared evaluator and agent runtime from `_common/install_harness.sh`;
4. a non-root `agent` working directory at `/tmp/agent`.

The agent invokes the installed engineering software directly.

## Updating an image

A toolchain update can move oracle output and therefore requires review as a
benchmark environment change:

1. update the Dockerfile or `tools.sh` with exact pins;
2. rebuild the affected domain image;
3. rerun every affected oracle;
4. verify the task references and tolerances still hold, or recalibrate them
   with documented provenance; and
5. update [`VERSIONS.md`](VERSIONS.md).
