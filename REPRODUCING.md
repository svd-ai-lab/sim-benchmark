# Reproducing HWE-bench runs

A reproducible run records the dataset/task version, domain image contents,
agent, model, inference settings and resource budget.

## Published dataset

Install [Harbor](https://www.harborframework.com/docs/getting-started) and run
the published dataset:

```bash
uv tool install harbor
harbor run -d hwe-bench/hwe-bench -a oracle
```

Omitting a dataset tag selects `latest`. Use an explicit published tag when a
result must remain comparable over time. See
[HWE-bench on Harbor Hub](https://hub.harborframework.com/datasets/hwe-bench/hwe-bench/latest).

To evaluate a model, replace `oracle` with a Harbor-supported agent and supply
the model and credentials required by that adapter.

## Local checkout

The repository contains task definitions and the Dockerfiles for the three
domain images. Build the domain you need from the repository root:

```bash
git clone https://github.com/svd-ai-lab/hwe-bench
cd hwe-bench
environment/domains/build.sh combustion --intl
environment/domains/build.sh battery --intl
environment/domains/build.sh cfd --intl
```

Then run a local task or subdomain:

```bash
harbor run -p cases/battery/cells \
  -i a123_lfp_discharge_1p15c_298k -a oracle
```

Use the local path for development. Use the published dataset for a clean
consumer-side check of what Harbor Hub serves.

## Environment identity

Tasks currently reference local image tags named
`sim-benchmark-<track>-fullstack:latest`. These are retained technical
identifiers. The tag alone is not a reproducibility pin: record the repository
commit and image digest with a run.

The Dockerfiles pin their base images by digest and the track toolchains pin the
primary solver and Python packages. Current solver versions are recorded in
[`environment/domains/VERSIONS.md`](environment/domains/VERSIONS.md) and in
each task's `tests/kpis.json`.

## Results

Harbor writes jobs and trial artifacts locally. Preserve at least:

- the task/dataset version;
- agent and model configuration;
- image digest;
- `reward.json` and `reward_detail.json`;
- agent and verifier logs; and
- completion, timeout and infrastructure status.

Do not compare rows produced from different task contracts or evaluator/image
contents as if they were one benchmark version. See
[`LEADERBOARD.md`](LEADERBOARD.md) for reporting rules.

## Regional mirrors

`environment/domains/build.sh --intl` selects Docker Hub, PyPI and npm sources
suitable for international build hosts. Without `--intl`, the Dockerfiles use
the repository's CN-oriented defaults. Mirror selection changes where packages
are downloaded from, not their pinned versions.
