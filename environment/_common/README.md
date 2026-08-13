# Shared domain-image setup

`install_harness.sh` installs the components shared by the battery, combustion
and CFD task images:

- the HWE-bench verifier package;
- Node.js and the agent CLIs Harbor may invoke;
- the token-usage proxy helpers used by supported agent configurations; and
- a non-root `agent` user.

Domain solvers and scientific Python packages are installed by each domain's
`tools.sh` before the shared setup runs.

## Dockerfile use

Each domain Dockerfile uses the repository root as its build context:

```dockerfile
COPY lib/sim_benchmark_verifier /opt/sim-benchmark-verifier/
COPY tools/openai_usage_proxy.py /opt/openai_usage_proxy.py
COPY tools/ccr-plugins /opt/ccr-plugins/
COPY environment/_common/install_harness.sh /tmp/install_harness.sh
RUN bash /tmp/install_harness.sh
```

Build mirrors and the agent user ID can be overridden with the arguments
documented in `environment/domains/build.sh`.
