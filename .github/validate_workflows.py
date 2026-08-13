"""Reject a workflow file GitHub would silently refuse to load.

Run it before pushing any change under `.github/workflows/`:

    uv run python .github/validate_workflows.py

Why this exists, and why it is not in `tools/`: it guards the layer that runs
everything else. Keeping it next to the files it checks makes its scope clear.

The failure it is built for is real and has happened twice. A step with two
`run:` keys made GitHub reject the entire file; every job disappeared, the runs
showed "This run likely failed because of a workflow file issue", and several
merges went through with nothing gating them. It survived local review because
**PyYAML's default loader accepts duplicate mapping keys** and quietly keeps
the last one -- so the file parsed fine for anyone who checked it with
`yaml.safe_load`. The loader below raises instead, which is the whole point;
the structural checks after it are a cheap bonus.

If `lint-cases.yaml` itself is invalid, GitHub cannot start this check. Repository
branch protection must therefore require the workflow's validation job.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).parent / "workflows"

STEP_KEYS = {
    "name",
    "id",
    "if",
    "uses",
    "run",
    "with",
    "env",
    "shell",
    "working-directory",
    "continue-on-error",
    "timeout-minutes",
}
JOB_KEYS = {
    "name",
    "runs-on",
    "steps",
    "needs",
    "if",
    "env",
    "strategy",
    "outputs",
    "permissions",
    "concurrency",
    "container",
    "services",
    "timeout-minutes",
    "continue-on-error",
    "defaults",
    "environment",
    "uses",
    "secrets",
}


class NoDuplicateKeys(yaml.SafeLoader):
    """SafeLoader that raises on a duplicate mapping key instead of dropping it."""


def _construct_mapping(loader: yaml.Loader, node: yaml.Node, deep: bool = False) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r} -- GitHub refuses to load the whole file",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


NoDuplicateKeys.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def check(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        doc = yaml.load(path.read_text(encoding="utf-8"), Loader=NoDuplicateKeys)
    except yaml.YAMLError as exc:
        return [f"{path}: does not parse\n{exc}"]

    if not isinstance(doc, dict):
        return [f"{path}: top level is not a mapping"]

    # YAML 1.1 resolves a bare `on:` to the boolean True, so look under both.
    if doc.get("on", doc.get(True)) is None:
        problems.append(f"{path}: no `on:` trigger block -- nothing would ever run it")

    jobs = doc.get("jobs") or {}
    if not jobs:
        problems.append(f"{path}: declares no jobs")

    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            problems.append(f"{path}: job {job_id} is not a mapping")
            continue
        unknown = sorted(set(job) - JOB_KEYS)
        if unknown:
            problems.append(f"{path}: job {job_id}: unknown job keys {unknown}")
        if "uses" not in job and not job.get("runs-on"):
            problems.append(f"{path}: job {job_id}: no `runs-on`")
        for i, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                problems.append(f"{path}: job {job_id} step {i} is not a mapping")
                continue
            unknown = sorted(set(step) - STEP_KEYS)
            if unknown:
                problems.append(f"{path}: job {job_id} step {i}: unknown keys {unknown}")
            if ("run" in step) == ("uses" in step):
                problems.append(
                    f"{path}: job {job_id} step {i}: needs exactly one of `run` / `uses`"
                )
    return problems


def main() -> int:
    paths = sorted(WORKFLOWS.glob("*.y*ml"))
    if not paths:
        print(f"no workflow files under {WORKFLOWS}", file=sys.stderr)
        return 1

    problems: list[str] = []
    for path in paths:
        found = check(path)
        problems.extend(found)
        if not found:
            print(f"ok   {path.name}")

    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
