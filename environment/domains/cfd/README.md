# CFD image

`sim-benchmark-cfd-fullstack:latest` provides OpenFOAM ESI v2412 with Gmsh and
the Python packages used for mesh handling and post-processing.

Build from the repository root:

```bash
environment/domains/build.sh cfd --intl
```

Smoke-test the primary tools:

```bash
docker run --rm sim-benchmark-cfd-fullstack:latest bash -lc \
  'source /etc/profile; simpleFoam -help >/dev/null; gmsh --version; \
   python3 -c "import meshio, pyvista; print(meshio.__version__, pyvista.__version__)"'
```

CFD tasks invoke OpenFOAM tools directly. Add or update domain dependencies in
`tools.sh`; do not add a separate launcher layer.
