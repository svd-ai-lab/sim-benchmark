#!/usr/bin/env python3
"""
lint_case.py — validate a sim-benchmark case against SCHEMA.md.

Usage:
    python tools/lint_case.py cases/<domain>/<case-id>
    python tools/lint_case.py cases/cfd/fluids/*/         # sweep a track
    python tools/lint_case.py cases/_template     # should pass (placeholders are "TODO" but fields exist)

Exit code 0 = all checks pass. Exit code 1 = one or more failures (diagnostics printed).

Checks:
    Structural — required files exist at the expected paths.
    task.toml  — schema_version, required sections, required fields, enum values.
    Executable — git records mode 100755 for solve.sh and tests/test.sh. The
                 *index* is read, not the filesystem, so the check says the same
                 thing on every platform (see `check_executable`).

Does NOT check: that solve.sh runs green, that reward.json is produced. Those are
Phase 1 concerns (oracle run + harbor run), not schema lint.
"""

from __future__ import annotations

import argparse
import ast
import functools
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

REQUIRED_FILES = ["task.toml", "instruction.md", "tests/test.sh"]

REQUIRED_TASK_FIELDS = ["name", "description", "authors", "keywords"]
# Schema 1.1 remains accepted for the historical case set. New pilot tasks use
# Harbor 0.20's schema 1.3 names; keeping both here makes migration incremental
# without pretending the legacy names are understood by current Harbor.
REQUIRED_RUNTIME_FIELDS = {
    "1.1": {
        "environment": ["cpus", "memory_gb", "internet_access"],
        "agent": ["timeout_s", "execution_user"],
        "verifier": ["timeout_s", "execution_user"],
    },
    "1.3": {
        "environment": ["cpus", "memory_mb", "storage_mb", "network_mode"],
        "agent": ["timeout_sec", "user"],
        "verifier": ["timeout_sec", "user"],
    },
}
ORG_NAME_RE = r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$"
REQUIRED_METADATA_SIM_FIELDS = [
    "solver",
    "source_type",
    "difficulty_tier",
    "release_status",
    "oracle_status",
    "score_template",
    "leakage_risk",
    "capability_target",
]

DETECTOR_DIR = (
    Path(__file__).resolve().parent.parent
    / "lib/sim_benchmark_verifier/sim_benchmark_verifier/detectors"
)


def known_solvers() -> set[str]:
    """Solver tags the verifier can actually gate.

    `solver` picks the anti-cheat artifact detector, so a tag with no detector
    silently skips the gate. Deriving the allowed set from the detectors
    directory means dropping in `detectors/<solver>.py` is what permits the
    tag — the list cannot drift behind the code the way a literal does.
    `neutral` opts out on purpose (declared solver-agnostic, no gate).
    """
    found = {
        p.stem
        for p in DETECTOR_DIR.glob("*.py")
        if p.stem not in {"__init__", "universal"}
    }
    return found | {"neutral"}


ENUMS = {
    # `neutral` = case is solver-agnostic and takes no artifact gate.
    # A concrete tag must have a detector; see known_solvers(). A case driving
    # more than one tool joins them with '+' (e.g. cadquery+calculix) and every
    # component is checked.
    "solver": known_solvers(),
    # The documented vocabulary is CLAUDE.md's [metadata.sim] block; these sets
    # track it. A value in use but not listed there is drift to resolve one way
    # or the other, not something to legitimize by widening the enum quietly.
    # `tutorial_variant` is deliberately absent (#150). It is a *demand channel*
    # -- the directory list under docs/demand_sources/records/ -- and the case
    # field that carries a channel is `source_channel = "tutorial-variant"`.
    # `source_type` says what kind of thing the reference is, and for a case
    # derived from a toolchain's example that is `tutorial`. Two spellings for
    # one fact is what let "tutorial is smoke-test only" and "unperturbed is
    # smoke-test only" disagree in the authoring skill for as long as they did.
    "source_type": {"paper", "vv_standard", "novel_variant", "tutorial",
                    "forum", "github", "workshop", "standard"},
    "difficulty_tier": {"S", "M", "L", "H"},
    "release_status": {"public_runnable", "public_draft", "hidden_eval", "private_only"},
    "oracle_status": {"available", "deferred", "not_applicable"},
    "score_template": {"measurement", "numerical", "workflow"},
    "capability_target": {"setup", "solver_execution", "numerical", "postprocess",
                          "debugging", "physics", "solve"},
    # gt_type is optional these days (per-KPI ground truth lives in kpis.json).
    "gt_type": {"analytical", "experimental", "high-fidelity",
                "high-fidelity-solver", "paper-reported", "committee"},
}

SUPPORTED_SCHEMA_VERSIONS = frozenset(REQUIRED_RUNTIME_FIELDS)

# The release statuses under which a case is actually run and scored. Both are
# "runnable now"; `hidden_eval` differs only in that the case itself is not
# published. `public_draft` lacks the runnable assets and `private_only` is off
# Harbor entirely, so neither produces a number.
SCORED_RELEASE_STATUSES = frozenset({"public_runnable", "hidden_eval"})


def _metadata_sim_field(root: Path, field: str) -> str | None:
    p = root / "task.toml"
    if not p.is_file():
        return None
    try:
        with p.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError:
        return None
    metadata = data.get("metadata")
    sim = metadata.get("sim") if isinstance(metadata, dict) else None
    if isinstance(sim, dict):
        value = sim.get(field)
        if isinstance(value, str):
            return value
    return None


def check_files(root: Path, errors: list[str]) -> None:
    for rel in REQUIRED_FILES:
        p = root / rel
        if not p.is_file():
            errors.append(f"missing file: {rel}")
    task_data = None
    try:
        with (root / "task.toml").open("rb") as file:
            task_data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError):
        pass
    environment = task_data.get("environment", {}) if isinstance(task_data, dict) else {}
    if not environment.get("docker_image") and not (root / "environment/Dockerfile").is_file():
        errors.append("missing file: environment/Dockerfile (or set [environment].docker_image)")
    if _metadata_sim_field(root, "oracle_status") == "available":
        solve = root / "solution" / "solve.sh"
        if not solve.is_file():
            errors.append("missing file: solution/solve.sh")


# The entry points Harbor exec()s. `100755` is the only mode git has for
# "executable"; it does not record group/other bits separately.
EXECUTABLE_ENTRY_POINTS = ("solution/solve.sh", "tests/test.sh")
GIT_EXECUTABLE_MODE = "100755"


@functools.lru_cache(maxsize=64)
def _repo_root(start: Path) -> Path | None:
    """Nearest ancestor of `start` holding a `.git` — no subprocess.

    A linked worktree's `.git` is a *file* rather than a directory, which is why
    this tests existence rather than `is_dir()`: several agents share this
    checkout and every one of them lints from a worktree.
    """
    for d in (start, *start.parents):
        if (d / ".git").exists():
            return d
    return None


@functools.lru_cache(maxsize=8)
def _git_index_modes(root: Path) -> dict[str, str] | None:
    """Every tracked path under `root` → the file mode git records for it.

    One `git ls-files` for the whole repository rather than one per file: the
    live sweep is ~130 cases and a subprocess per entry point would dominate it.
    `-z` so the paths come out literal (git quotes non-ASCII names otherwise).

    None means there is no index to read — git missing, or a tree that was
    rsync'd to a compute host and is not a checkout at all. That is a different
    state from "tracked and wrong" and from "untracked", and it is the only one
    where this check has nothing to say: the tree has no relationship to what CI
    would get, so there is no claim to make about it either way.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-s", "-z"],
            capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        proc = None
    if proc is None or proc.returncode != 0:
        print(
            f"note: cannot read git's index at {root} — the executable-bit "
            f"check is skipped, because the mode CI sees is the one git "
            f"records and there is no index here to read it from",
            file=sys.stderr,
        )
        return None
    modes: dict[str, str] = {}
    for entry in proc.stdout.decode("utf-8", "replace").split("\0"):
        head, _, path = entry.partition("\t")
        if path:
            modes[path] = head.split()[0]
    return modes


def check_executable(root: Path, errors: list[str]) -> None:
    """`solve.sh` / `test.sh` must be mode 100755 **in git's index**.

    Read the index, never the filesystem. `os.access(p, os.X_OK)` and
    `st_mode & S_IXUSR` answer a question about the working tree, and on Windows
    the working tree has no executable bit to answer it with: `os.access` returns
    True unconditionally, and `stat` reports 0o100666 for a file git records as
    100755. So the old check passed for every case on every Windows machine — it
    ran, and on the machine this repo is developed on it could not see the thing
    it was checking.

    The cost of that is on record. PR #253 went red in CI on three lines, two of
    them dropped executable bits, and none of the three was visible locally;
    the failure reads as CI being flaky, and re-running it produces the same red
    forever. What CI checks out is what git *records*, so that is what the lint
    has to read — and `git ls-files -s`'s first column says the same thing on
    Windows, Linux and macOS.

    The fix message names `git update-index --chmod=+x` rather than `chmod +x`
    on purpose: with `core.filemode = false` (git's default on Windows) `chmod`
    changes nothing git will ever notice, so a reader who reaches for it goes in
    a circle.

    Untracked is reported, not passed over. A file git does not know about has no
    recorded mode, so the honest answer is "cannot tell", and saying that is
    strictly better than the two alternatives: green would reproduce exactly the
    local-green/CI-red split this check exists to close, and red would name a
    mode the file does not have yet.
    """
    case_root = root.resolve()
    repo_root = _repo_root(case_root)
    if repo_root is None:
        return
    modes = _git_index_modes(repo_root)
    if modes is None:
        return
    for rel in EXECUTABLE_ENTRY_POINTS:
        if not (root / rel).is_file():
            continue
        try:
            key = (case_root / rel).relative_to(repo_root).as_posix()
        except ValueError:                      # outside the repo it lives in
            continue
        mode = modes.get(key)
        if mode is None:
            errors.append(
                f"{rel} is not tracked by git, so the mode CI will see does not "
                f"exist yet. `git add {key}` first — then, if it lands 100644, "
                f"`git update-index --chmod=+x {key}`"
            )
        elif mode != GIT_EXECUTABLE_MODE:
            errors.append(
                f"git records mode {mode} for {rel}, not {GIT_EXECUTABLE_MODE} "
                f"— CI checks out what git records, so this lands "
                f"non-executable there however it looks locally. Fix with: "
                f"git update-index --chmod=+x {key}   "
                f"(`chmod +x` does nothing git notices when core.filemode is "
                f"false, which is the default on Windows)"
            )


def check_fields(section: dict, required: list[str], path: str, errors: list[str]) -> None:
    for f in required:
        if f not in section:
            errors.append(f"{path}: missing field '{f}'")


def check_enum(value: object, field: str, path: str, errors: list[str]) -> None:
    allowed = ENUMS[field]
    # `solver` may name more than one tool, '+'-joined; each needs a detector.
    parts = (
        value.split("+")
        if field == "solver" and isinstance(value, str)
        else [value]
    )
    for part in parts:
        if part not in allowed:
            errors.append(f"{path}.{field}: '{part!r}' not in {sorted(allowed)}")


def check_harbor_native_contract(root: Path, errors: list[str]) -> None:
    """Reserved hook for task-level Harbor contract checks.

    Stripped invariants (no longer load-bearing):

    - "instruction.md must not mention OpenFOAM tutorials": removed when the
      tutorial-derived case set was archived (2026-04-26). The contamination
      risk it guarded against doesn't exist for the canonical neutral set;
      keeping the check forced bizarre wording on every new case.
    - "Dockerfile must install tmux + asciinema": those are Harbor terminus-2
      requirements; we run claude-code via the agent_harness path which
      doesn't shell out to tmux. Cases inherit from sim-benchmark-base which
      installs whatever Harbor needs.

    Remaining invariant:

    - tests/verify.py (or test.sh) is the verifier entry point. The newer
      cases delegate to lib/sim_benchmark_verifier via tests/test.sh and
      don't ship a verify.py — no need to enforce the I/O path string here
      since the verifier library encodes that contract centrally.
    """
    return  # all dynamic invariants now live in the verifier library


def check_solver_label_agreement(root: Path, errors: list[str]) -> None:
    """`tests/kpis.json` may restate the solver label; if it does, it must agree.

    The duplication is deliberate and the disagreement is what needs catching.
    `task.toml` is the catalog field, but under a separate verifier container the
    evaluator only receives `tests/`, so `task.toml` is unreachable and the
    scorer falls back to the copy in `kpis.json`. A None label does not raise —
    it *skips* the artifact gate — so a stale copy here silently turns the
    anti-cheat off for that case, or points it at the wrong detector, and the
    score looks entirely normal either way.
    """
    kpis_path = root / "tests" / "kpis.json"
    if not kpis_path.is_file():
        return
    try:
        declared = json.loads(kpis_path.read_text(encoding="utf-8")).get("solver")
    except (json.JSONDecodeError, OSError):
        return
    if declared is None:
        return
    authoritative = _metadata_sim_field(root, "solver")
    if authoritative and declared != authoritative:
        errors.append(
            f"tests/kpis.json: solver={declared!r} disagrees with task.toml "
            f"[metadata.sim].solver={authoritative!r} — the evaluator reads the "
            f"kpis.json copy when task.toml is out of reach, so the mismatch "
            f"decides which anti-cheat detector runs"
        )
    if declared not in known_solvers():
        errors.append(
            f"tests/kpis.json: solver={declared!r} has no detector in "
            f"{sorted(known_solvers())}"
        )


CURRENT_SCHEMA_VERSION = "1.3"


def check_current_schema(root: Path, errors: list[str]) -> None:
    """Require the current contract — used on cases a PR *adds*.

    1.1 stays valid for the historical set, but a new case written on it grows
    the migration backlog instead of the benchmark. This is the only check that
    cares whether a case is new, so the caller decides what "new" means (CI
    diffs against the base ref); the check itself just asserts the version.
    """
    toml_path = root / "task.toml"
    if not toml_path.is_file():
        return
    try:
        with toml_path.open("rb") as fh:
            version = tomllib.load(fh).get("schema_version")
    except tomllib.TOMLDecodeError:
        return  # the normal lint already reports the parse error
    if version != CURRENT_SCHEMA_VERSION:
        errors.append(
            f"task.toml: new cases must use schema_version "
            f"{CURRENT_SCHEMA_VERSION!r}, got {version!r}. Copy a reference case "
            f"(cases/cfd/fluids/lid_driven_cavity_ghia_re100 or "
            f"cases/combustion/kinetics/ch4_air_idt_phi1p02_1418k_1p3atm), not an "
            f"older track — see CLAUDE.md \"Reference cases\"."
        )


def _manifest_versions() -> dict[str, str]:
    """`docker_image` stem → the version token VERSIONS.md records for it.

    VERSIONS.md is the single source of truth (CLAUDE.md, "Toolchain versions
    are an environment-layer concern"), and it is a markdown table, so this
    reads the table rather than duplicating the numbers here. A second copy of
    a version number is a second thing to bump and the one nobody bumps.
    """
    manifest = REPO / "environment" / "domains" / "VERSIONS.md"
    out: dict[str, str] = {}
    if not manifest.is_file():
        return out
    for line in manifest.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 4 or not cells[1].startswith("`"):
            continue
        image = cells[1].strip("`")
        # `v` REQUIRED. Without it the row for `eda-digital-asic-fullstack`
        # yields "2024" — out of the date in `ORFS commit 902652c1
        # (2024-12-15)` — and the check would then demand that an OpenROAD
        # commit hash contain a year. A guard whose reference value is itself
        # guessed is worse than no guard, so this only fires on rows that state
        # a version unambiguously (`ESI v2412`), which today means cfd.
        m = re.search(r"\bv(\d{4})\b", cells[3])
        if m:
            out[image] = m.group(1)
    return out


def check_oracle_version_matches_the_manifest(root: Path, errors: list[str]) -> None:
    """A declared `<solver>_version` has to agree with VERSIONS.md.

    CLAUDE.md requires the match and nothing has ever enforced it, which is
    visible in the values themselves: across the cfd track the same fact is
    written three ways (`ESI v2412`, `ESI OpenFOAM v2412`, and one carrying the
    full build string). Free text is fine — a build string is strictly more
    provenance than the bare tag, so this does not demand one spelling. What it
    refuses is a version that names a DIFFERENT toolchain than the image the
    case declares, which is the failure that makes a stored gt_value
    unreproducible while looking documented.
    """
    kp = root / "tests" / "kpis.json"
    tp = root / "task.toml"
    if not (kp.is_file() and tp.is_file()):
        return
    try:
        prov = (json.loads(kp.read_text(encoding="utf-8"))
                .get("oracle_provenance") or {})
        with tp.open("rb") as fh:
            image = ((tomllib.load(fh).get("environment") or {})
                     .get("docker_image") or "")
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, OSError):
        return  # the normal lint already reports the parse error
    declared = {k: v for k, v in prov.items()
                if k.endswith("_version") and isinstance(v, str)}
    if not declared:
        return          # absence is issue #40's subject, not this check's
    want = _manifest_versions().get(image.split(":")[0].replace("sim-benchmark-", ""))
    if not want:
        return          # image not in the manifest — a different defect
    # Digit-bounded, not a bare substring. `want in value` accepts a version
    # that merely CONTAINS the digits: `ESI v2506, build 20241201` passes a
    # 2412 manifest because the build date holds "2412". That is a false
    # negative on precisely the drift this check exists to catch — and it is
    # the same date-vs-version confusion already guarded against on the
    # manifest side in `_manifest_versions`.
    hit = re.compile(rf"(?<![0-9])v?{re.escape(want)}(?![0-9])")
    for key, value in declared.items():
        if not hit.search(value):
            errors.append(
                f"tests/kpis.json oracle_provenance.{key} = {value!r} does not "
                f"name the toolchain {image!r} ships ({want} per "
                f"environment/domains/VERSIONS.md). A gt_value calibrated on a "
                f"different version is not reproducible from this image."
            )


def check_oracle_provenance_is_recorded(root: Path, errors: list[str]) -> None:
    """A new case has to say how its reference value was obtained.

    Not enforced on the historical set: four cfd cases carry an entirely empty
    `oracle_provenance` and three more have no extraction method (#40), and the
    fix for those is to go and find out what was actually run — not to make
    something up so a linter passes. This stops the next one instead.
    """
    kp = root / "tests" / "kpis.json"
    if not kp.is_file():
        return
    try:
        prov = (json.loads(kp.read_text(encoding="utf-8"))
                .get("oracle_provenance") or {})
    except (json.JSONDecodeError, OSError):
        return
    if not prov:
        errors.append(
            "tests/kpis.json: oracle_provenance is empty — a case has to record "
            "where its gt_value came from (what was run, or which publication "
            "it is quoted from). See cases/cfd/fluids/naca0012_subsonic."
        )
        return
    method = prov.get("method") or prov.get("extract_method") or ""
    reference = prov.get("reference") or prov.get("gt_provenance") or ""
    if len(method) < 40 and not reference:
        errors.append(
            "tests/kpis.json: oracle_provenance records neither a reproducible "
            "'method' (>= 40 chars: solver, model, grid, how the KPI was "
            "extracted) nor a published 'reference' the value is quoted from."
        )


def check_demand_record_is_linked(root: Path, errors: list[str]) -> None:
    """A new case has to name the demand it came from, and the record has to exist.

    Step 1 of the case pipeline (CLAUDE.md, "Case sources"): a case enters the
    funnel with a demand record behind it, so that "who asked for this" is
    answerable later by reading rather than by remembering. `demand_record`
    already exists in the schema and until now nothing checked it —
    `tools/source_coverage_matrix.py` reads it to draw a table, which is a
    report and not a gate.

    Not enforced on the historical set, and the measurement is why: 114 of 128
    live cases carry no `demand_record` at all — every case in combustion,
    battery and packaging, plus five in cfd — while all 14 that do carry one
    resolve to a file that exists. A blanket error would fail 89% of the repo on
    the day it landed, and a check that does that gets switched off rather than
    satisfied. So this gates growth, exactly as `check_current_schema` does.

    What it cannot establish is that the channel is real. A fabricated YAML
    passes. What it buys is that the claim is written down somewhere a reviewer
    can look, which is the difference between an unsupported case and an
    unsupported case nobody can find.

    One record may serve many cases: a generated family sources once for the
    whole family (#323), so uniqueness is deliberately not checked.
    """
    p = root / "task.toml"
    if not p.is_file():
        return
    try:
        with p.open("rb") as fh:
            sim = (tomllib.load(fh).get("metadata") or {}).get("sim") or {}
    except tomllib.TOMLDecodeError:
        return  # the normal lint already reports the parse error
    rec = (sim.get("demand_record") or "").strip()
    if not rec:
        errors.append(
            "task.toml: [metadata.sim].demand_record is missing — a new case "
            "names the demand record it came from "
            "(docs/demand_sources/records/<channel>/<domain>/<id>.yaml). A "
            "generated family may point every member at one record."
        )
        return
    if not (REPO / rec).is_file():
        errors.append(
            f"task.toml: demand_record {rec!r} does not exist. A link to a "
            "missing record is worse than none: it reads as sourced."
        )
        return
    check_operating_point_has_provenance(REPO / rec, errors)


def check_operating_point_has_provenance(record: Path, errors: list[str]) -> None:
    """The record has to say what fixes the operating point, not just the `gt`.

    `gt_value`'s provenance was argued across a dozen issues; the operating
    point had no rule at all, and nothing required a case to say why 1.43C
    rather than 1.4 or 1.5 (#503). The generator says as much in its own words:
    the nudge "keeps the regime and the difficulty identical while making the
    answer reachable only by running the case" -- honest, and it buys
    unrecallability and nothing else.

    So this asks for the block, and **only that it is present**. Whether
    `fixed_by` is a real artefact or the honest `nudged` is a REVIEW judgement,
    for the same reason `check_demand_record_is_linked` does not try to tell a
    real channel from a fabricated one: a check that cannot be applied without
    domain expertise gets satisfied rather than answered. What it buys is that
    "we chose this number for no reason" becomes something written down where a
    reviewer looks, instead of something nobody was ever asked.

    Parsed by hand rather than with a YAML library: this file's linting is
    standard-library only, and the block's presence is a top-level key, which is
    a line-start match. A record whose `operating_point` is commented out or
    nested under something else therefore reads as absent -- which is the safe
    direction, since the failure it guards is silence.
    """
    try:
        text = record.read_text(encoding="utf-8")
    except OSError:
        return                       # the resolve check above already spoke
    if any(ln.startswith("operating_point:") for ln in text.splitlines()):
        return
    errors.append(
        f"demand record {record.name}: no `operating_point:` block -- a new "
        "case has to say what fixes its operating point (a spec limit, a "
        "standard's clause, a duty cycle, a warranty condition), per "
        "docs/demand_sources/SCHEMA.md. `fixed_by: nudged` is a legal answer "
        "and passes this check; it is review that judges it."
    )


def check_task_toml(root: Path, errors: list[str]) -> None:
    p = root / "task.toml"
    if not p.is_file():
        return

    try:
        with p.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        errors.append(f"task.toml: invalid TOML — {e}")
        return

    schema_version = data.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            "task.toml: schema_version must be one of "
            f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}, got {schema_version!r}"
        )
        schema_version = "1.1"

    for section_name, required in (
        ("task", REQUIRED_TASK_FIELDS),
        ("environment", REQUIRED_RUNTIME_FIELDS[schema_version]["environment"]),
        ("agent", REQUIRED_RUNTIME_FIELDS[schema_version]["agent"]),
        ("verifier", REQUIRED_RUNTIME_FIELDS[schema_version]["verifier"]),
    ):
        section = data.get(section_name)
        if not isinstance(section, dict):
            errors.append(f"task.toml: missing or malformed [{section_name}] section")
            continue
        check_fields(section, required, f"task.toml [{section_name}]", errors)

    if schema_version == "1.3":
        environment = data.get("environment", {})
        if environment.get("network_mode") not in {"public", "no-network", "allowlist"}:
            errors.append(
                "task.toml [environment].network_mode: must be public, no-network, or allowlist"
            )
        verifier = data.get("verifier", {})
        if verifier.get("environment_mode") == "separate":
            verifier_environment = verifier.get("environment")
            if not isinstance(verifier_environment, dict):
                errors.append(
                    "task.toml [verifier]: separate mode requires [verifier.environment]"
                )
            elif verifier_environment.get("network_mode") == "public":
                compose_path = root / "tests" / "docker-compose.yaml"
                compose_text = (
                    compose_path.read_text(encoding="utf-8")
                    if compose_path.is_file()
                    else ""
                )
                if not re.search(r"(?m)^\s*network_mode:\s*none\s*$", compose_text):
                    errors.append(
                        "Harbor separate verifier with public policy must enforce "
                        "tests/docker-compose.yaml network_mode: none"
                    )
            elif verifier_environment.get("network_mode") != "no-network":
                errors.append(
                    "task.toml [verifier.environment].network_mode: must be no-network, "
                    "or public with Docker Compose network_mode: none"
                )
            if not (root / "tests" / "Dockerfile").is_file():
                errors.append(
                    "missing tests/Dockerfile for Harbor separate verifier image"
                )
        artifacts = data.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            errors.append("task.toml: schema 1.3 tasks must declare at least one artifact")
        elif any(not isinstance(item, str) or not item.startswith("/") for item in artifacts):
            errors.append("task.toml: artifact paths must be absolute POSIX paths")

    # The agent container needs network access to its model endpoint. The
    # separate verifier container is isolated by its Docker Compose policy.

    task = data.get("task")
    if isinstance(task, dict):
        name = task.get("name")
        if isinstance(name, str) and (not re.match(ORG_NAME_RE, name) or ".." in name):
            errors.append(
                f"task.toml [task].name: must match 'org/name' format (alphanumeric + -/_/., "
                f"single slash). Got: {name!r}"
            )
        if schema_version == "1.3" and isinstance(name, str) and name.count("/") != 1:
            errors.append(
                "task.toml [task].name: Harbor schema 1.3 requires exactly org/name"
            )
        authors = task.get("authors")
        if isinstance(authors, list):
            for i, a in enumerate(authors):
                if not isinstance(a, dict) or "name" not in a:
                    errors.append(
                        f"task.toml [task].authors[{i}]: each author must be a table like "
                        f"{{name=\"...\"}}, not a bare string"
                    )

    metadata = data.get("metadata")
    sim = metadata.get("sim") if isinstance(metadata, dict) else None
    if not isinstance(sim, dict):
        errors.append("task.toml: missing [metadata.sim] section")
        return

    check_fields(sim, REQUIRED_METADATA_SIM_FIELDS, "task.toml [metadata.sim]", errors)

    for enum_field in ENUMS:
        if enum_field in sim:
            check_enum(sim[enum_field], enum_field, "task.toml [metadata.sim]", errors)

    # task_id names the solver-neutral task this case instantiates. Optional on
    # a one-off case (the case id is then the task id), but when present it has
    # to be portable: a dash-slug that carries no solver name, so every port of
    # the task can share it verbatim.
    task_id = sim.get("task_id")
    if task_id is not None:
        if not isinstance(task_id, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", task_id
        ):
            errors.append(
                "task.toml [metadata.sim].task_id: must be a lowercase dash-slug "
                f"(no underscores, no spaces). Got: {task_id!r}"
            )
        else:
            solver_words = {
                s for s in known_solvers() if s != "neutral"
            }
            if any(word in task_id.split("-") for word in solver_words):
                errors.append(
                    "task.toml [metadata.sim].task_id: must not name a solver — "
                    "the task is what ports across solvers, the case is the "
                    f"landing site. Got: {task_id!r}"
                )

    # prototype_origin names the published example a case is a variant of;
    # prototype_delta says what moved. The authoring skill makes the second
    # REQUIRED whenever the first is set, and the reason is that origin alone
    # reads as a confession with no defence: it says the setup collides with
    # something a model has seen, and leaves unanswered whether the collision
    # was perturbed away. Half a record is worse than none, because it looks
    # like a record.
    #
    # Presence is required on top of the pair whenever `source_type =
    # "tutorial"`: that value says the reference *is* a tutorial, so the case
    # has a prototype by construction and silence about it is not a check that
    # was run, it is a check that was skipped. Silence is only admissible from
    # a case whose reference is not an example at all.
    #
    # The rule spent three issues unenforced and the reason kept changing, so
    # the dead reasons are recorded once here rather than left to be believed.
    # First it would have failed the whole battery track (#161); #202
    # backfilled those fifty and that reason died. Then it would have failed
    # exactly one live case, `cases/cfd/fluids/channel_developing_entry`, which
    # was green and correct -- and that is not the rule being premature, it is
    # the rule being mis-keyed: `prototype_origin`'s documented form was
    # `<tool>:<path in the example set>`, which a case descending from a
    # third-party tutorial has no value for (#221). Widening the field so a URL
    # or other locatable reference also counts was the schema decision that
    # unblocked this, and SCHEMA.md §7 is where it is settled. The rule may not
    # be re-derived from `source_type` alone as a licence to demand a
    # first-party path.
    #
    # #150 settled the other half: `source_type` is not what decides
    # leaderboard eligibility -- perturbation is -- so this pair evidences the
    # perturbation rather than gating on the enum value. Requiring the record
    # on a tutorial case is not the same claim as requiring a low leak risk;
    # `leakage_risk = 3` has its own check below.
    origin = sim.get("prototype_origin")
    delta = sim.get("prototype_delta")
    for fname, value in (("prototype_origin", origin), ("prototype_delta", delta)):
        if value is not None and not (isinstance(value, str) and value.strip()):
            errors.append(
                f"task.toml [metadata.sim].{fname}: must be a non-empty string"
            )
    if origin and not (isinstance(delta, str) and delta.strip()):
        errors.append(
            "task.toml [metadata.sim]: prototype_origin is set but prototype_delta "
            "is missing — a case that names the example it derives from has to say "
            "what it moved off it, or the record cannot be audited"
        )
    if delta and not (isinstance(origin, str) and origin.strip()):
        errors.append(
            "task.toml [metadata.sim]: prototype_delta is set but prototype_origin "
            "is missing — say which example the delta is measured against"
        )
    if sim.get("source_type") == "tutorial" and not (
        isinstance(origin, str) and origin.strip()
    ):
        errors.append(
            'task.toml [metadata.sim]: source_type = "tutorial" requires '
            "prototype_origin + prototype_delta — the reference is an example, so "
            "the case has a prototype, and an unperturbed published example shipped "
            "as a case measures recall. Name it (`<tool>:<path/in/the/example/set>` "
            "for the toolchain's own set, a URL or other locatable reference for a "
            "third-party one) and say what moved"
        )

    if "leakage_risk" in sim:
        risk = sim["leakage_risk"]
        if not isinstance(risk, int) or risk < 0 or risk > 3:
            errors.append("task.toml [metadata.sim].leakage_risk: must be an integer 0..3")
        # `leakage_risk = 3` is the author saying the answer is recallable
        # verbatim -- a textbook problem with the number in the chapter, or an
        # example shipped unperturbed. A case that says that about itself and
        # is also scored is not a measurement of anything, so the two
        # declarations may not coexist (#150). The statuses below are the ones
        # a trial is actually run and scored under; `public_draft` (no runnable
        # assets) and `private_only` (off Harbor) are not, so a high-leakage
        # case can sit in either of those without contradiction.
        elif risk == 3 and sim.get("release_status") in SCORED_RELEASE_STATUSES:
            errors.append(
                "task.toml [metadata.sim]: leakage_risk = 3 with release_status = "
                f"{sim.get('release_status')!r} -- a case whose answer is recallable "
                "verbatim cannot also be scored. Perturb the operating point (and "
                "record it in prototype_delta) or drop the release_status; lowering "
                "the declared risk is not the fix"
            )

    if "tags" in sim and not (isinstance(sim["tags"], list) and all(isinstance(t, str) for t in sim["tags"])):
        errors.append("task.toml [metadata.sim].tags: must be a list of strings")

    # KPI definitions live in `tests/kpis.json` (read by sim_benchmark_verifier).
    # The legacy [[metadata.sim.kpis]] array in task.toml is no longer required;
    # cross-validate that tests/kpis.json exists and parses if present.
    kpis_json = root / "tests" / "kpis.json"
    if kpis_json.is_file():
        try:
            data_kpis = json.loads(kpis_json.read_text(encoding="utf-8"))
            kpis = data_kpis.get("kpis")
            groups = data_kpis.get("kpi_groups")
            if not isinstance(kpis, dict) or not kpis:
                errors.append("tests/kpis.json: top-level `kpis` must be a non-empty object")
            if not isinstance(groups, dict) or not groups:
                errors.append("tests/kpis.json: top-level `kpi_groups` must be a non-empty object")
            if isinstance(kpis, dict) and isinstance(groups, dict):
                total = sum(float(g.get("weight", 0)) for g in groups.values() if isinstance(g, dict))
                if abs(total - 1.0) > 1e-6:
                    errors.append(f"tests/kpis.json: kpi_groups weights must sum to 1.0, got {total}")
                for kname, kspec in kpis.items():
                    if not isinstance(kspec, dict):
                        errors.append(f"tests/kpis.json: kpi {kname!r} must be an object")
                        continue
                    group = kspec.get("group")
                    if group not in groups:
                        errors.append(
                            f"tests/kpis.json: kpi {kname!r} declares unknown group {group!r}"
                        )
                    # The tolerance band, under its current spelling. The
                    # verifier still reads `T_good`/`T_bad` so a stored trial
                    # and an unmigrated `_phase2` case keep working, but a live
                    # case may not carry the old names — a single-letter prefix
                    # that nothing in the repo defines is what got read as a
                    # temperature next to a `T_K` column (#188).
                    for old, new in (("T_good", "pass_tol"),
                                     ("T_bad", "gross_error_tol"),
                                     ("T_good_source", "pass_tol_source"),
                                     ("T_good_pct", "pass_tol_pct"),
                                     ("T_bad_pct", "gross_error_tol_pct")):
                        if old in kspec:
                            errors.append(
                                f"tests/kpis.json: kpi {kname!r} uses the retired "
                                f"field {old!r} — rename it to {new!r}"
                            )
                    if kspec.get("pass_tol") is None:
                        errors.append(
                            f"tests/kpis.json: kpi {kname!r} has no `pass_tol` — the "
                            f"absolute tolerance, in the KPI's own unit, that the "
                            f"value must land inside to score"
                        )
                for gname, gspec in groups.items():
                    weight = float(gspec.get("weight", 0)) if isinstance(gspec, dict) else 0.0
                    members = [
                        kname
                        for kname, kspec in kpis.items()
                        if isinstance(kspec, dict) and kspec.get("group") == gname
                    ]
                    if weight > 0 and not members:
                        errors.append(
                            f"tests/kpis.json: kpi_group {gname!r} has positive weight but no KPIs"
                        )
        except json.JSONDecodeError as e:
            errors.append(f"tests/kpis.json: invalid JSON — {e}")
    else:
        errors.append("missing tests/kpis.json (verifier reads KPI specs from here)")


# How much room the container's verifier timeout must leave above whatever
# internal timeout the evaluator enforces itself. The gap covers container start,
# the evaluator's imports and the mechanism/parameter load — measured at a few
# seconds, so this is deliberately generous rather than tight.
VERIFIER_TIMEOUT_MARGIN_S = 60


def check_verifier_outlasts_its_own_timeout(root: Path, errors: list[str]) -> None:
    """`[verifier].timeout_sec` must exceed the evaluator's internal timeout.

    The two are different things and only one of them produces a measurement. A
    shape-2 evaluator re-runs the submission under `reproduction_timeout_s` and,
    when that fires, still writes a `reward.json` saying the reproduction timed
    out — a scored zero, attributable to the submission. If the container's own
    cap fires first, the evaluator is killed mid-flight and writes nothing at all,
    which is not a zero but an absent measurement: the case silently drops out of
    the sweep and its row reads as if nobody ran it.

    So the ordering is load-bearing and it is currently only a convention: every
    case here sets the container cap 120 s above the internal one. Raising a
    `reproduction_timeout_s` without raising the cap alongside it would invert
    them, and nothing about the result would say so.
    """
    spec_p = root / "tests" / "spec.json"
    toml_p = root / "task.toml"
    if not (spec_p.exists() and toml_p.exists()):
        return
    try:
        spec = json.loads(spec_p.read_text(encoding="utf-8"))
        data = tomllib.loads(toml_p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, tomllib.TOMLDecodeError):
        return          # both are reported by their own checks
    internal = spec.get("reproduction_timeout_s")
    cap = (data.get("verifier") or {}).get("timeout_sec")
    if not isinstance(internal, (int, float)) or not isinstance(cap, (int, float)):
        return
    if cap < internal + VERIFIER_TIMEOUT_MARGIN_S:
        errors.append(
            f"[verifier].timeout_sec = {cap} leaves only {cap - internal}s above "
            f"tests/spec.json reproduction_timeout_s = {internal}; needs at least "
            f"{VERIFIER_TIMEOUT_MARGIN_S}s, or the container is killed before the "
            f"evaluator can write a reward.json and the trial has NO measurement "
            f"rather than a scored zero"
        )


def check_evaluator_names_its_own_case(root: Path, errors: list[str]) -> None:
    """Everything under tests/ that names a case must name THIS case.

    The evaluator stamps its `case_id` into `reward_detail.json`, and that is the
    only record of which contract produced a score. A case built by copying a
    sibling and not updating the id therefore files every one of its results
    under the sibling's name — and it is indistinguishable, from the outside,
    from the container having actually been handed the wrong tests/ directory,
    which is a separate and much worse fault. One case shipped this way and its
    scores looked entirely normal.
    """
    case_id = root.name
    spec_p = root / "tests" / "spec.json"
    if spec_p.exists():
        try:
            got = json.loads(spec_p.read_text(encoding="utf-8")).get("case_id")
        except (json.JSONDecodeError, OSError):
            got = None
        if got and got != case_id:
            errors.append(f"tests/spec.json case_id = {got!r}, but this case is "
                          f"{case_id!r}")
    kpis_p = root / "tests" / "kpis.json"
    if kpis_p.exists():
        try:
            got = json.loads(kpis_p.read_text(encoding="utf-8")).get("case_id")
        except (json.JSONDecodeError, OSError):
            got = None
        if got and got != case_id:
            errors.append(f"tests/kpis.json case_id = {got!r}, but this case is "
                          f"{case_id!r}")
    vn_p = root / "tests" / "verify_native.py"
    if vn_p.exists():
        try:
            text = vn_p.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for m in re.finditer(r'case_id\s*=\s*["\']([^"\']+)["\']', text):
            if m.group(1) != case_id:
                errors.append(f"tests/verify_native.py passes "
                              f"case_id={m.group(1)!r}, but this case is "
                              f"{case_id!r}")


AGENT_WORKDIR_PREFIX = "/tmp/agent"
# The two names whose fallback argument is legitimately an absolute path: at
# rerun time the runner sets both, and they resolve to the evaluator's own copy.
# `Allrun` setting `SIM_BENCH_SUBMISSION="$PWD"` is what makes the literal dead.
_ENV_LOOKUP = ("environ", "getenv")
_SHELL_FALLBACK = re.compile(r"\$\{(?:SIM_BENCH_SUBMISSION|AGENT_WORKDIR|"
                             r"ORACLE_CASE|ORACLE_HERE)(?::?-)")


def _submission_sources(root: Path) -> list[Path]:
    """The files `solution/solve.sh` copies INTO the submission.

    Read out of the case's own oracle script rather than assumed from a
    directory name, because the live set puts them in three different places:
    `$HERE/case/` (cfd), `$HERE/run_case.py` (combustion, battery) and
    `$WORK/case/` (the repair-shaped cases, whose starting fixtures are staged
    by the runner out of `environment/`). All 128 live cases resolve.
    """
    solve = root / "solution" / "solve.sh"
    if not solve.is_file():
        return []
    try:
        text = solve.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[Path] = []
    for line in text.splitlines():
        m = re.match(r"\s*cp\s+(?:-\w+\s+)*(?P<args>\S.*?)\s*$", line)
        if not m:
            continue
        parts = [p.replace('"', "").replace("'", "")
                 for p in m.group("args").split()]
        if len(parts) < 2:
            continue
        dest, srcs = parts[-1], parts[:-1]
        if not dest.startswith(("$SUB", "$SUBMISSION", "$CASE_RUN")):
            continue
        for s in srcs:
            # `$HERE` is the case's solution/; `$WORK` is the agent workdir,
            # which the runner fills from the case's environment/.
            base = ({"$HERE": root / "solution", "$WORK": root / "environment"}
                    .get(s.split("/", 1)[0]))
            if base is None:
                continue
            rel = s.split("/", 1)[1].rstrip("/.") if "/" in s else ""
            out.append(base / rel if rel else base)
    return out


def _hardcoded_agent_paths(path: Path) -> list[tuple[int, str]]:
    """`(line, text)` for every RUNTIME mention of /tmp/agent in one file.

    Prose is not the target, so a `.py` file is read through its AST and only
    string constants that survive into the code are examined -- a docstring
    explaining this very rule must not trip it. Everything else (a bash
    `Allrun`, a `run_case.sh`) is scanned as text with whole-line comments
    dropped.

    An env-var fallback is not a hardcoded path: `os.environ.get("ORACLE_CASE",
    "/tmp/agent/case")` names where the value comes from, and the runner always
    sets it. The literal is the last resort, not the address being used.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if AGENT_WORKDIR_PREFIX not in text:
        return []

    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        docstrings = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        allowed: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                spelling = ast.dump(node.func)
                if any(name in spelling for name in _ENV_LOOKUP):
                    allowed.update(id(a) for a in node.args)
        hits = []
        for node in ast.walk(tree):
            # `in`, not `startswith`: these scripts shell out through
            # `subprocess.run(["bash", "-lc", f"... {path} ..."])`, so a path
            # that matters is as likely to sit in the middle of a command
            # string as at the start of its own literal. That is what makes
            # the docstring exclusion above load-bearing rather than
            # decorative -- under `startswith` no docstring could ever have
            # matched, and removing it changed no test.
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and AGENT_WORKDIR_PREFIX in node.value
                    and id(node) not in docstrings and id(node) not in allowed):
                hits.append((node.lineno, node.value))
        return hits

    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#") or AGENT_WORKDIR_PREFIX not in line:
            continue
        if _SHELL_FALLBACK.search(line):
            continue
        hits.append((i, line.strip()))
    return hits


def check_submission_entry_is_self_contained(root: Path, errors: list[str]) -> None:
    """Nothing the submission ships may name an absolute path outside itself.

    The evaluator copies the submission into a fresh directory under `/tmp` and
    re-executes its own entry point; the oracle path additionally mounts the
    original `/tmp/agent` **read-only**, because a grading container must not be
    able to modify the submission it is grading. A run that reaches outside its
    own copy therefore depends on the state of a path that is not part of what
    was handed over -- and under the read-only mount it does not merely read
    stale data, it dies.

    `bump_in_channel_2d` and `naca0012_subsonic` both wrote a vestigial
    `/tmp/agent/result.json` *after* writing the contract's `results.csv`
    correctly, and both scored 0.0 for it: the numbers were right, the
    reproduction was killed by a write no consumer reads (#368). Nothing had
    caught it because neither case had ever reached the evaluator -- their
    meshes live in `environment/`, which the oracle runner did not stage until
    #345, so for their whole life they died earlier and faster.

    What makes this admissible rather than decorative is the same test a
    scoring gate takes: it fails a submission the tolerance band passes. Those
    two were inside their bands on every KPI.
    """
    for src in _submission_sources(root):
        files = [src] if src.is_file() else (
            sorted(f for f in src.rglob("*") if f.is_file()) if src.is_dir() else [])
        for f in files:
            for lineno, what in _hardcoded_agent_paths(f):
                what = what if len(what) <= 80 else what[:77] + "..."
                errors.append(
                    f"{f.relative_to(root)}:{lineno} names {AGENT_WORKDIR_PREFIX} "
                    f"outside the submission ({what!r}); this file is copied into "
                    f"the submission and re-run by the evaluator from a clean "
                    f"copy with /tmp/agent read-only, so the rerun fails. Derive "
                    f"the path from SIM_BENCH_SUBMISSION / ORACLE_CASE instead, "
                    f"or drop the write if nothing reads it (#368)")


def check_enforced_limits_are_stated(root: Path, errors: list[str]) -> None:
    """A limit the evaluator enforces has to appear in `instruction.md`.

    The contract must be complete from `instruction.md` + `tests/` alone, and a
    budget the agent cannot see is not part of a contract — it is a trap.

    This exists because of one that cost a whole sub-family. The flame-speed
    prompts asked, in so many words, that the reported speed be *grid-converged*,
    and said nothing about time. The evaluator re-ran the submission under a
    900 s limit. An agent that did what it was told wrote a grid-refinement loop,
    overran, and scored zero; an agent that did a single cheap solve passed. All
    three flame-speed trials failed that way in one sweep. Stating the limit —
    one sentence, generated from this same `spec.json` value — took them to 3/3
    passing, one of them reproducing in 610 s against the 900 s it now knew
    about.

    Checked by looking for the number, because a paraphrase without it ("finish
    promptly") is not something an agent can design against.
    """
    spec_p = root / "tests" / "spec.json"
    ins_p = root / "instruction.md"
    if not (spec_p.exists() and ins_p.exists()):
        return
    try:
        limit = json.loads(spec_p.read_text(encoding="utf-8")).get(
            "reproduction_timeout_s")
        prompt = ins_p.read_text(encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        return          # reported by their own checks
    if not isinstance(limit, (int, float)):
        return
    if str(int(limit)) not in prompt:
        errors.append(
            f"tests/spec.json enforces reproduction_timeout_s = {int(limit)}, "
            f"but instruction.md never states it. A budget the agent cannot see "
            f"is not part of the contract: it silently zeroes a submission that "
            f"did what the prompt asked for"
        )


MIN_REPRODUCTION_MARGIN = 3.0
# A timed correct submission is a much better basis than the oracle, so the
# margin over it only has to cover machine-speed spread (2.4x measured).
MIN_MEASURED_MARGIN = 2.4
# Seconds the container must still have after the reproduction deadline fires,
# for the evaluator to finish scoring and write `reward.json`. The two limits
# are not interchangeable: the inner one produces a scored zero, the outer one
# produces no measurement at all. Every live case already leaves at least 120 s,
# so this floor fails nothing today -- it is here because the harbor skill has
# been telling readers it was enforced while the code only required the outer
# limit to be larger by any amount, and a 1 s gap passed.
MIN_VERIFIER_HEADROOM_S = 60


def check_reproduction_budget_clears_the_oracle(root: Path, errors: list[str]) -> None:
    """A reproduction budget must not sit inside its own oracle's spread.

    The budget is a ceiling on the evaluator's rerun, and it costs nothing when
    it is not hit — so it must be sized where it cannot fail a correct
    submission. Sized tightly it produces a zero that is indistinguishable from
    bad physics, and which machine judged the trial decides it.

    That is on record. `lid_driven_cavity_ghia_re3200` carried a 600 s budget
    against its own 487 s oracle: two submissions timed out at exactly 600 s,
    and the single one that passed used 522 s of it. Its container limit was
    tighter still, so one trial was killed before the evaluator could write
    `reward.json` at all and stored as `unmeasured` rather than as a score.
    `ercoftac_periodic_hill_re10595` was worse on paper — a 710 s oracle against
    a 720 s budget, 1% of margin — and had simply not been unlucky yet.

    3x is the floor, not the target: the machine-speed spread measured across
    this repo's own hosts for one deterministic case is 2.4x on its own, before
    a submission is allowed to be finer than the oracle.

    **A case with no `oracle_wall_sec` is unmeasured, not exempt.** This check
    used to return on the missing field, so 30 of the 128 live cases -- 23% --
    passed it with nothing compared against anything, and a green lint said so
    (#340). That is the worse of the two failures it can have, because the
    number is what CLAUDE.md hangs the whole "the budget is part of the task"
    criterion on: without it a trial that ran out of budget cannot be told from
    a budget we set too small, which is our defect rather than the model's.
    A zero is refused for the same reason -- it would satisfy the presence
    check and skip the ratio below, which is the hole this closes wearing a
    different hat. An oracle that finishes in under a second records 1.
    """
    spec_p, toml_p = root / "tests" / "spec.json", root / "task.toml"
    if not (spec_p.is_file() and toml_p.is_file()):
        return
    try:
        spec = json.loads(spec_p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return          # reported by its own check
    budget = spec.get("reproduction_timeout_s")
    if not isinstance(budget, (int, float)):
        return
    found = re.search(r"oracle_wall_sec\s*=\s*(-?\d+)", _read(toml_p))
    if not found:
        errors.append(
            f"task.toml [metadata.sim] declares no oracle_wall_sec, so "
            f"tests/spec.json reproduction_timeout_s = {int(budget)} is checked "
            f"against nothing and this case passes on absence. Measure it — "
            f"solution/solve.sh plus the evaluator's clean reproduction, one "
            f"core, on the domain image — and record it. A case whose oracle "
            f"cost is unknown cannot say whether a burned agent budget was "
            f"the model's doing or ours"
        )
        return
    oracle = int(found.group(1))
    if oracle <= 0:
        errors.append(
            f"task.toml oracle_wall_sec = {oracle} is not a positive whole "
            f"number of seconds, so the margin below divides by it and cannot "
            f"be computed. An oracle finishing in under a second records 1 — a "
            f"zero here skips exactly the check a missing field used to"
        )
        return
    # Prefer a timed correct submission over the oracle, because the oracle
    # under-predicts: one measured submission ran 4.2x its case's reference.
    measured = spec.get("measured_submission_wall_sec")
    basis, cost, margin = "oracle cost", oracle, MIN_REPRODUCTION_MARGIN
    if isinstance(measured, (int, float)) and measured > 0:
        basis, cost, margin = "measured correct submission", measured, MIN_MEASURED_MARGIN
    if budget < margin * cost:
        errors.append(
            f"tests/spec.json reproduction_timeout_s = {int(budget)} is only "
            f"{budget / cost:.1f}x this case's {basis} ({int(cost)} s); at least "
            f"{margin:.1f}x is required. A budget this close to a run known to be "
            f"correct fails it on a slower host, and the zero it produces cannot "
            f"be told from bad physics"
        )
    verifier = re.search(r"\[verifier\][\s\S]*?timeout_sec\s*=\s*(\d+)", _read(toml_p))
    if verifier and int(verifier.group(1)) < budget + MIN_VERIFIER_HEADROOM_S:
        errors.append(
            f"[verifier].timeout_sec = {verifier.group(1)} leaves "
            f"{int(verifier.group(1)) - int(budget)} s after tests/spec.json "
            f"reproduction_timeout_s = {int(budget)}; at least "
            f"{MIN_VERIFIER_HEADROOM_S} s is required. The container is otherwise "
            f"killed before the evaluator can write reward.json, which stores as "
            f"`unmeasured` rather than as a score"
        )


# Ruled 2026-08-12 on #616, both on the added-case path only.
#
# The two bound different things and neither substitutes for the other. The
# oracle ceiling bounds what *grading* costs us: the evaluator's own re-run is
# oracle-scale, and one that does not fit its `RERUN_TIMEOUT` at the case's
# declared `cpus` destroys the audit tally the anti-cheat gate reads, which is
# how `ge_2025_ch4_nh3_n2o_ar_lbv` had a perfect submission reported as a
# hard-coder (#278). The agent ceiling bounds what *one rollout* costs a buyer,
# which is the first thing a buyer's technical diligence asks because RL needs
# thousands of them.
MAX_ORACLE_WALL_SEC = 120
# What 110 of the 130 live cases already declare, so it writes down what the
# in-scope tracks do rather than setting a stretch target. It is a floor under
# carelessness and not a budget to spend up to: a case built in the
# ship-the-expensive-state shape should land nearer the 600 s that the tree's two
# repair-shaped cases declare (#624).
MAX_AGENT_TIMEOUT_SEC = 1800


def check_declared_cost_is_inside_the_ceilings(root: Path, errors: list[str]) -> None:
    """A new case declares an oracle cost and an agent budget inside the ceilings.

    CLAUDE.md's four admission criteria put **cost** first, and it is the one row
    of that table with no step of its own — discriminability has the shortcut
    screen, non-leakage has `lint_agent_visible`, the contract has
    `contract_freeze`, and cost had only the ratio check above, which reads the
    *relation* between two declared numbers and never their size.

    `ge_2025_ch4_nh3_n2o_ar_lbv` is what that costs: a finished case with a
    complete contract and acceptance evidence, whose declared worst case was
    4.7 h per trial against a live-set median of 0.78 h. It scored 1.0 on
    18 vCPU and 0.0 at the `cpus = 2` it declares, because its own evaluator
    re-run did not fit its own `RERUN_TIMEOUT`. It went to `_pending/` on cost
    (#278) — after it was built.

    **Growth only**, like `check_demand_record_is_linked` and
    `check_current_schema`, and the measurement is why: nine live cases exceed
    the oracle ceiling and eighteen exceed the agent ceiling — twenty-one
    distinct cases, a sixth of the live set. Every one of them is grandfathered.
    A check that failed that much of the repo on the day it landed would be
    switched off rather than satisfied, and re-litigating an existing case's
    declared numbers is a different decision from refusing a new one.

    **Why this is not folded into the ratio check above**, which the ruling
    suggested: that one is unconditional — `lint_one` calls it for every case —
    so a ceiling placed inside it would fire on the whole tree, which is the
    opposite of what was ruled. The added-case path registers whole functions,
    so the ceilings ride it as one.

    **Presence of `oracle_wall_sec` is mandated here rather than inherited**,
    and the reason is measured. The ratio check's own presence branch looks
    unconditional and is not: it returns early when `tests/spec.json` is missing
    or carries no `reproduction_timeout_s`. All 130 live cases have both, so it
    bites on the whole live set — but `ge_2025` has no `tests/spec.json` at all,
    which is exactly why it declares no `oracle_wall_sec` and nothing has ever
    complained. So a new case in that same hand-built shape would slip the
    presence check *and* the ceiling built on top of it, and the gate would miss
    its own motivating case a second time. It costs the backlog nothing: every
    live case already declares the field. `[agent].timeout_sec` needs no
    equivalent — `REQUIRED_RUNTIME_FIELDS` already demands it at schema 1.3,
    which `check_current_schema` requires on this same path.
    """
    p = root / "task.toml"
    if not p.is_file():
        return
    try:
        with p.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return  # the normal lint already reports the parse error
    sim = (data.get("metadata") or {}).get("sim") or {}

    oracle = sim.get("oracle_wall_sec")
    if not _is_number(oracle):
        errors.append(
            "task.toml: [metadata.sim].oracle_wall_sec is missing — a new case "
            "declares what its own reference solution costs, in whole seconds, "
            "at the `cpus` the case declares. Measure it: solution/solve.sh "
            "plus the evaluator's clean reproduction, on the domain image. A "
            "ceiling on a field a case may omit binds nothing, which is how "
            "the case this gate exists to catch presented it with nothing to "
            "compare (#278, #616)"
        )
    elif oracle > MAX_ORACLE_WALL_SEC:
        errors.append(
            f"task.toml: [metadata.sim].oracle_wall_sec = {int(oracle)} s exceeds "
            f"the {MAX_ORACLE_WALL_SEC} s ceiling a new case is admitted under. "
            f"This bounds what grading costs us — the evaluator's own re-run is "
            f"oracle-scale, and one that does not fit its RERUN_TIMEOUT destroys "
            f"the audit tally the anti-cheat gate reads and reports a correct "
            f"submission as a hard-coder (#278). Make the reference solution "
            f"cheaper, or argue the exception in review. Existing cases over the "
            f"line are grandfathered: this fires on added cases only (#616)"
        )

    agent = (data.get("agent") or {}).get("timeout_sec")
    if _is_number(agent) and agent > MAX_AGENT_TIMEOUT_SEC:
        errors.append(
            f"task.toml: [agent].timeout_sec = {int(agent)} s exceeds the "
            f"{MAX_AGENT_TIMEOUT_SEC} s ceiling a new case is admitted under. "
            f"This bounds what one rollout costs a buyer, which is the first "
            f"thing a buyer's technical diligence asks because RL needs "
            f"thousands of them. The ceiling is not the target — a case built in "
            f"the ship-the-expensive-state shape should land nearer 600 s "
            f"(#624). Existing cases over the line are grandfathered: this fires "
            f"on added cases only (#616)"
        )


def _is_number(v: object) -> bool:
    """A declared budget, and not `true` — `bool` is an `int` in Python."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


_SAMPLE_SETS = re.compile(r"(?m)^\s*type\s+sets\s*;")
_SAMPLE_VECTOR = re.compile(
    r"\(\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s+"
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s+"
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s*\)")
# Any of these means the coordinate is derived from the submission's own mesh,
# or checked against bounds the case pins and states.
_BOUNDS_AWARE = ("install_sample_dict", "reproduced_mesh_bounds", "run_cavity")


def check_sampling_plane_is_derived_not_assumed(root: Path, errors: list[str]) -> None:
    """An evaluator sample coordinate must come from the mesh, not from a literal.

    A 2D case states its in-plane domain and asks for "a one-cell-thick slab".
    The slab's spanwise extent is therefore the submission's to choose, and both
    conventions are correct — z in [0, t] like the OpenFOAM tutorials, or
    z in [-t/2, +t/2] centred on zero. An evaluator dictionary naming a literal
    spanwise coordinate silently picks one: the point lands outside the other's
    mesh, sampling returns nothing, and a converged, mesh-clean, physically
    correct run scores zero for a convention no prompt ever stated.

    The cost is on record twice. The Ghia cavity family hit it first and fixed
    it locally, after three otherwise-correct submissions. Nobody swept the rest
    of the tree, so the same literal was still live in three more cases two
    weeks later, where it took five further board rows across three models —
    including one case that scored 0.0 for *every* model while its own oracle
    scored 1.0, because only the oracle's geometry could be sampled.

    Passing means the case either derives the coordinate from the reproduced
    mesh, or pins the mesh bounds and enforces them (which makes the literal
    part of a contract the submission was told about).
    """
    tests = root / "tests"
    if not tests.is_dir():
        return
    dicts = [p for p in tests.iterdir()
             if p.is_file() and p.suffix not in (".py", ".json", ".sh", ".yaml")]
    literal = [p.name for p in dicts
               if _SAMPLE_SETS.search(_read(p)) and _SAMPLE_VECTOR.search(_read(p))]
    if not literal:
        return
    source = "".join(_read(p) for p in tests.glob("*.py"))
    if any(token in source for token in _BOUNDS_AWARE):
        return
    errors.append(
        f"evaluator sample dictionary names literal coordinates ({', '.join(literal)}) "
        f"while tests/*.py never derives them from the reproduced mesh "
        f"({' / '.join(_BOUNDS_AWARE)}). A submission whose slab sits elsewhere in "
        f"the free direction then scores zero on a run it solved correctly"
    )


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


_REQUIRED_NAME_LOOP = re.compile(
    r"for\s+(\w+)\s+in\s+\(([^)]*)\)\s*:\s*\n"
    r"(?:[^\n]*\n){0,3}?[^\n]*\{\s*\1\s*\}", re.M)
_FO_PATCHES = re.compile(r"(?m)^\s*(?:patches\s*\(([^)]*)\)|name\s+([A-Za-z_]\w*))\s*;")


def check_required_names_are_stated(root: Path, errors: list[str]) -> None:
    """A patch name the evaluator insists on has to be mandated by the prompt.

    Boundary-patch names are the submission's to choose: a case states the
    physics of each boundary and never what to call it. So a verifier that greps
    a submitted field file for the literal `top` is testing vocabulary, and the
    submission that names its far field `topWall` fails a case it solved.

    That is not hypothetical. Three cfd cases gated on names no prompt stated
    (`top`, `lower_wall_downstream`, `hotWall`), and across the stored trials
    fifteen runs that had reached `exit_code 0` with a converged solution were
    scored zero on the spelling — one of them after 3000 iterations and 445 s.
    The repair is role discovery: find each boundary by what it does, and keep
    requiring that every role be filled exactly once.

    So the rule this enforces is narrow: if a name is load-bearing, `instruction.md`
    must mandate it as a literal — in backticks, the way this repo writes literals
    the agent must reproduce. Naming it in prose ("the top boundary at y=1") is
    not a naming instruction, and that gap is exactly what the trap was made of.
    """
    tests = root / "tests"
    ins_p = root / "instruction.md"
    if not (tests.is_dir() and ins_p.exists()):
        return
    try:
        prompt = ins_p.read_text(encoding="utf-8")
    except OSError:
        return
    mandated = set(re.findall(r"`([A-Za-z_][\w/.]*)`", prompt))

    verifiers = " ".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in tests.glob("*.py") if p.is_file())
    required: set[str] = set()
    for _var, items in _REQUIRED_NAME_LOOP.findall(verifiers):
        required.update(re.findall(r"[\"'](\w+)[\"']", items))
    # A function-object dictionary the evaluator ships names the patch it will
    # sample. That is only a naming mandate if the verifier copies it verbatim;
    # rewriting it with a discovered name is the fix, not a violation.
    for dict_path in sorted(tests.iterdir()):
        if not dict_path.is_file() or not dict_path.name.startswith("evaluator"):
            continue
        if f'"{dict_path.name}"' in verifiers and ".replace(" in verifiers:
            continue
        text = dict_path.read_text(encoding="utf-8", errors="replace")
        for group, single in _FO_PATCHES.findall(text):
            required.update(group.split() if group else [single])

    unstated = sorted(name for name in required if name not in mandated)
    if unstated:
        errors.append(
            f"tests/ requires the submission to use the patch name(s) "
            f"{unstated}, which instruction.md never mandates as a literal. "
            f"Discover each boundary by its role (type, value, position) and "
            f"require the role to be filled, or state the name in the prompt — "
            f"a name-only gate zeroes runs that solved the case"
        )


def check_output_interface_is_stated(root: Path, errors: list[str]) -> None:
    """The file and columns the evaluator reads must be literals in the prompt.

    Under the output-interface contract the evaluator reads exactly one file and
    the columns `tests/spec.json` names, and nothing else (CLAUDE.md, "The output
    interface"). That removes the seam a per-case grader had for encoding the
    oracle's choices -- and leaves precisely one way for the same class of defect
    to come back: the prompt and the spec disagreeing about what to call things.

    It is not hypothetical either; it is the shape of `bump_in_channel_2d`, which
    scored 0.0 for all five models because its prompt described the deliverable's
    keys in a table while the scorer wanted a different structure, and every
    model wrote exactly what the table described. Under this contract that becomes
    "missing required column", which is the same zero with a better error message
    -- so the check has to run before the sweep, not after.

    Bare containment, not a regex over prose: a column named in `spec.json` has to
    appear verbatim in `instruction.md`. The prompt is free to say more.
    """
    spec_path = root / "tests" / "spec.json"
    ins_path = root / "instruction.md"
    if not (spec_path.is_file() and ins_path.is_file()):
        return
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        prompt = ins_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        return
    interface = spec.get("interface")
    if not isinstance(interface, dict):
        return

    missing = [name for name in
               ([interface.get("file")] if interface.get("file") else [])
               + list(interface.get("columns") or [])
               if name and name not in prompt]
    if missing:
        errors.append(
            f"tests/spec.json declares the output interface {missing}, which "
            f"instruction.md never names. The evaluator reads only that file and "
            f"those columns, so a disagreement here scores every submission zero "
            f"on 'missing required column' however good the physics"
        )

    # The derivations have to name columns the interface actually carries, or the
    # case fails at scoring time on a host, having already paid for the solve.
    # `labels` counts as declared. A relation derivation names a `key` column
    # holding the configuration NAMES, and the evaluator requires that column to
    # be in `labels` rather than `columns` precisely so it is read as text
    # instead of parsed as a number (`csv_interface._pair` refuses it otherwise).
    # Checking only `columns` therefore rejected the one shape the evaluator
    # demands -- unnoticed until now because `pair_delta` and `pair_ratio` had
    # tests but no live case (#487 is the first).
    declared = set(interface.get("columns") or []) | set(interface.get("labels") or [])
    for kpi, derivation in (spec.get("kpis") or {}).items():
        if not isinstance(derivation, dict):
            continue
        referenced = {v for k, v in derivation.items()
                      if k in ("x", "y", "key", "value") and isinstance(v, str)}
        unknown = sorted(referenced - declared)
        if unknown:
            errors.append(
                f"tests/spec.json KPI {kpi!r} derives from column(s) {unknown}, "
                f"which interface.columns does not declare: {sorted(declared)}"
            )


_STORE_FACT = re.compile(
    r"scoreboard_withheld|runs_disowned|contract_gate|scoreboard_runs|results-local"
    r"|\bboard cells?\b|\bon the board\b|\boff the board\b|\bleaderboard\b", re.I)

# Predicates a case may claim defend it, and the token that has to appear in the
# evaluator for the claim to be true. `detectors` covers the vaguer phrasings,
# which assert that *some* artifact check runs without naming which.
_GATE_CLAIMS = {
    "has_mesh_and_solution": "has_mesh_and_solution",
    "has_result_database": "has_result_database",
    "has_solver_evidence": "has_solver_evidence",
    "artifact gate": "detectors",
    "artifact detector": "detectors",
    "anti-cheat detector": "detectors",
}

# The things "The output interface" forbids an evaluator to read: the
# submission's own setup, and the quality or wall-resolution properties of the
# mesh the task left it free to choose. NOT the serialisation the solver-evidence
# gate reads -- `polyMesh`, a time directory, an `.frd` -- which is why `mesh`
# on its own is deliberately absent and `mesh quality` / `mesh cell centre` are
# present.
_SETUP_INSPECTION = re.compile(
    r"physics setup|case setup|solver settings"
    r"|mesh validity|mesh quality|mesh cell cent(?:re|er)|checkMesh"
    r"|non[- ]orthogonalit|skewness|aspect ratio"
    r"|wall[- ]resolution|wall under[- ]resolution|under[- ]resolution"
    r"|y\s*[+⁺]|yplus"
    r"|boundary condition|fvSchemes|fvSolution|relaxation factor"
    r"|discreti[sz]ation scheme", re.I)

# Grader-side voice: the nouns for the thing that scores, and third-person verbs
# that put something other than the agent in the subject position. This is what
# separates a claim about the grader from a requirement on the agent, and the
# separation is the whole precision of the rule: an instruction states its
# requirements imperatively ("Use a wall-resolved mesh with first-cell y+ <= 1")
# or in the second person ("your mesh must"), never as "verifies" or "accepts".
_GRADER_VOICE = re.compile(
    r"\bevaluator\b|\bverifier\b|\bgrader\b|\bscorer\b|\banti-cheat\b"
    r"|\bhard gates?\b|\bgates?\b|\bgated\b"
    r"|\bverifies\b|\bvalidates\b|\bchecks\b|\basserts\b|\benforces\b"
    r"|\brejects\b|\baccepts\b|\brequires\b|\bmeasures\b|\binspects\b", re.I)

# Sentence boundaries that do not fall inside a number. A stop counts unless it
# has a digit on *both* sides: `1.05` and `0.001` stay whole, while `0.001. It`
# and `y+ <= 1; the verifier` split. Requiring a non-digit on both sides instead
# was the first draft and it ran two sentences together whenever one ended in a
# number, which is most of them here. A single newline is not a boundary --
# prose in `instruction.md` wraps mid-sentence.
_SENTENCE_SPLIT = re.compile(r"(?<![0-9])[.;!?]|[.;!?](?![0-9])|\n\s*\n")

_UNFALSIFIABLE_CLAIM = re.compile(
    r"not recoverable from a closed[- ]form|\bno closed[- ]form\b"
    r"|there is no closed form|cannot be recalled|nothing to recall", re.I)

_CITATION = re.compile(r"#\d{1,4}")

_EVALUATOR_IMPORT = re.compile(
    r"from\s+sim_benchmark_verifier(?:\.(\w+))?\s+import|import\s+sim_benchmark_verifier\.(\w+)")


def _kpi_prose(root: Path) -> list[tuple[str, str]]:
    """Every string in `tests/kpis.json`, with a dotted path to where it sits."""
    p = root / "tests" / "kpis.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    out: list[tuple[str, str]] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, str):
            out.append((path or "<root>", node))
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(data, "")
    return out


@functools.cache
def _evaluator_sources(root_str: str) -> str:
    """The source of every shared evaluator this case's `tests/` imports.

    A case's `tests/verify*.py` is a three-line shim; the behaviour is in
    `lib/sim_benchmark_verifier`. Resolving the import is what turns "this case
    claims a gate" into a question with an answer.
    """
    root = Path(root_str)
    lib = REPO / "lib" / "sim_benchmark_verifier" / "sim_benchmark_verifier"
    text = []
    for shim in sorted((root / "tests").glob("*.py")):
        try:
            src = shim.read_text(encoding="utf-8")
        except OSError:
            continue
        text.append(src)
        for m in _EVALUATOR_IMPORT.finditer(src):
            mod = m.group(1) or m.group(2)
            if not mod:
                continue
            f = lib / f"{mod}.py"
            if f.is_file():
                try:
                    text.append(f.read_text(encoding="utf-8"))
                except OSError:
                    pass
    return "\n".join(text)


def check_kpi_prose_claims_only_what_it_can_know(root: Path, errors: list[str]) -> None:
    """`tests/kpis.json` may not assert what nothing can check.

    Eight times this repo has shipped a sentence describing its own machinery
    that was simply not true, and the reason is always the same: the sentence
    sits in a file no check reads, next to fields that several do. #211 is the
    template — thirteen cfd `kpis.json` asserted the openfoam detector zeroed
    them while it was never imported — and #264 is the same shape one layer out,
    a case claiming it had been withheld from a board it was on.

    The two rules below are the two answers that exist, and they are different:

    * **A store fact is not knowable from here at all.** Which store the board
      serves, what it holds, which cases reach it and what they scored are
      properties of `results-local/` and of the host serving `/api/scoreboard` —
      a case directory cannot open either, and CI cannot either. So there is
      nothing to verify and the rule is *do not write it*: measure it with
      `tools/board_store.py` / `tools/frozen_set.py` / `tools/power_analysis.py`
      and put the answer in the issue that asked. A number copied here is stale
      from the commit that writes it and, being unreadable by any check, stays
      believed.
    * **A gate claim IS knowable**, because the gate is code in this repo. So
      that one is verified rather than banned: resolve the evaluator the case's
      `tests/` actually imports and require the named predicate to appear in it.

    Both rules are about `tests/kpis.json` specifically, and that is deliberate.
    `CLAUDE.md` and the docs argue about the board on purpose — they are read by
    people, revised as a unit, and nothing downstream trusts them. `kpis.json` is
    the contract a grader reads.
    """
    prose = _kpi_prose(root)
    for path, s in prose:
        m = _STORE_FACT.search(s)
        if m:
            errors.append(
                f"tests/kpis.json {path}: names a store/board fact ({m.group(0)!r}). "
                f"A case directory cannot read the result store, so nothing here or in "
                f"CI can check the claim and it goes stale silently — measure it "
                f"(tools/board_store.py, tools/frozen_set.py, tools/power_analysis.py) "
                f"and record the answer in the issue instead"
            )

    claimed = {
        token
        for _, s in prose
        for phrase, token in _GATE_CLAIMS.items()
        if phrase.lower() in s.lower()
    }
    if not claimed:
        return
    src = _evaluator_sources(str(root))
    for token in sorted(claimed):
        if token not in src:
            errors.append(
                f"tests/kpis.json claims a gate this case's evaluator does not "
                f"implement: {token!r} appears in no module `tests/` imports from "
                f"sim_benchmark_verifier. Either make the code do it or drop the "
                f"sentence — a claimed gate is not a gate (#211)"
            )


def check_no_setup_inspection_is_claimed(root: Path, errors: list[str]) -> None:
    """No case file may say the grader inspects the submission's own setup.

    `check_kpi_prose_claims_only_what_it_can_know` resolves a *named* predicate
    against the evaluator that runs, which is exact and is why it misses this:
    its phrase table lists function names and two artifact-gate spellings, so a
    gate asserted in plain English is invisible to it. #483 found two that were
    — `flatplate_zpg_subsonic`'s prompt telling the agent the evaluator "also
    independently verifies the physics setup, mesh validity and wall
    resolution", and `turbulent_channel_flow_retau590` asserting hard gates on
    setup and mesh quality with the verifier accepting `y+ <= 1.05`. Both
    survived the #211 sweep, which was done by reading.

    **This rule needs no lookup, because both branches are defects.** If no such
    check exists, the sentence is the #7 / #211 class: a contract describing a
    grader that is not there, and the agent that reads it carefully is the one
    that pays. If the check does exist, it is what "The output interface"
    forbids outright — the evaluator reading `system/`, `constant/`, a boundary
    keyword or the wall spacing of a mesh the task left free — which is the
    defect that scored nine of nineteen cfd cases zero on correct physics. So
    the finding is decidable from the sentence alone, and it does not go stale
    when the evaluator is replaced.

    Two things keep it off honest writing. The object list holds only what the
    evaluator may not read, never the serialisation it may: a case saying the
    rerun must leave a `polyMesh` and a solved time directory is describing the
    solver-evidence gate, and that is admissible and passes. And a requirement
    on the *agent* is imperative or second-person — "Use a wall-resolved mesh
    with first-cell `y+ <= 1`" carries no grader voice and passes, while "the
    verifier accepts `y+ <= 1.05`" does and fails. The rule is what a case says
    the grader does, not what the task asks for.

    A denial fires too ("nothing reads the mesh quality"), and that is intended
    rather than tolerated: a case file records what the evaluator does, and an
    enumeration of what it does not do is the kind of sentence that is true when
    written and false two evaluators later. Put the retraction in the commit.

    **The word lists are calibrated against the tree, not guessed.** The first
    draft fired on 27 sentences across nine live cases, and 25 of them were
    honest writing of two shapes. Eight packaging cases record that a `gt_value`
    is "this repo's own oracle, and nothing external checks the setup that
    produced it" — a provenance statement about the oracle, not about the
    grader, which cost the object `the setup` its place; `physics setup` and
    `case setup` keep theirs and still catch the sentence #483 was opened on.
    And `retau590`'s `pass_tol_source` says the band "is not scoring
    wall-resolution noise the task leaves free" — band calibration, not a check,
    which cost `scoring` its place in the voice list. Nothing was lost: both
    #483 instances match on a different token each (`physics setup` +
    `verifies`, `mesh quality` + `hard gates`), which is what
    `tools/tests/test_kpi_prose_claims.py` pins with the historical text.
    """
    surfaces: list[tuple[str, str]] = [
        (f"tests/kpis.json {path}", s) for path, s in _kpi_prose(root)
    ]
    ins = root / "instruction.md"
    if ins.is_file():
        try:
            surfaces.append(("instruction.md", ins.read_text(encoding="utf-8")))
        except OSError:
            pass

    for where, text in surfaces:
        for sentence in _SENTENCE_SPLIT.split(text):
            if not sentence:
                continue
            voice = _GRADER_VOICE.search(sentence)
            obj = _SETUP_INSPECTION.search(sentence)
            if not (voice and obj):
                continue
            quoted = " ".join(sentence.split())
            if len(quoted) > 160:
                quoted = quoted[:157] + "..."
            errors.append(
                f"{where}: claims the grader inspects the submission's own setup "
                f"({voice.group(0)!r} + {obj.group(0)!r}) — {quoted!r}. Either it "
                f"is not true, or it is a check 'The output interface' forbids; "
                f"state what the evaluator reads instead (#483)"
            )


def check_unfalsifiable_claims_are_attributed(root: Path, errors: list[str]) -> None:
    """A claim nothing can verify may not be written in the bare assertive voice.

    "This KPI has no closed form" is the third kind of self-description, and it
    is neither of the two `check_kpi_prose_claims_only_what_it_can_know` handles:
    it is a claim about the world, so it is not banned, and no check can decide
    it, so it cannot be verified. What a check *can* decide is whether the
    sentence says where it was established. `channel_retau395_repair_closure`
    carried "not recoverable from a closed form" for its whole life while its
    twin had already retracted the identical sentence as wrong, and integrating
    the log law puts the closed form 0.89% from `gt` — inside the band. The
    assertion was never checked because nothing about its phrasing invited a
    check.

    So the rule is a citation, not a verdict: a `#NNN` in the same string, which
    is where the argument lives. That is cheap, it is decidable, and it converts
    an unfalsifiable sentence into a falsifiable pointer.

    **New cases only** (`--require-current-schema`, the gate CI applies to what a
    PR adds). Six live cases carry a bare form of this claim today -- measured,
    not estimated: `backstep_laminar_armaly_re389`, `cylinder_schafer_turek_2d1_cd`,
    `de_vahl_davis_natural_convection_ra1e4`, `kovasznay_flow_re40`,
    `lid_driven_cavity_ghia_re3200`, `plane_poiseuille_friction_factor`. Whether
    each is true is per-case physics work and two of them belong to #266, so
    this stops the next one rather than demanding a sweep as the price of
    landing the check.
    """
    for path, s in _kpi_prose(root):
        m = _UNFALSIFIABLE_CLAIM.search(s)
        if m and not _CITATION.search(s):
            errors.append(
                f"tests/kpis.json {path}: asserts {m.group(0)!r} with nothing to "
                f"check it against. No verifier and no linter can decide whether a "
                f"quantity has a closed form; cite the issue where it was "
                f"established (#NNN in this same field), or state the closed form "
                f"and its distance from gt_value the way "
                f"`channel_retau395_repair_closure` now does"
            )


_NO_NET_CLAIM = re.compile(
    r"internet access is disabled|no internet access|network(?: access)? is disabled"
    r"|offline environment", re.I)


def check_network_claim_matches_reality(root: Path, errors: list[str]) -> None:
    """An instruction may not claim a network restriction the environment lacks.

    The instruction enforces nothing — `[environment].network_mode` is the only
    control — so a sentence claiming otherwise is not a harmless belt-and-braces,
    it is a false statement in the contract. 31 of 50 cases carried
    "Internet access is disabled." while every one of them declared
    `network_mode = "public"`.

    The mismatch is not fixable in the obvious direction on every host. Harbor
    routes any non-`public` mode through an nftables + gost egress sidecar, and
    on the host these cases run on that sidecar breaks the agent's own model
    endpoint: three paid probes — `no-network`, `allowlist` with the endpoint
    passed as `--allow-agent-host`, and `allowlist` with it declared in
    `allowed_hosts` — all returned
    `Unable to connect to API (UNKNOWN_CERTIFICATE_VERIFICATION_ERROR)` and a
    0.0 that is not a capability result. So the prompt is what has to give.

    This check only fires in the direction that lies. Claiming no network while
    actually having none is fine.
    """
    ins_p = root / "instruction.md"
    toml_p = root / "task.toml"
    if not (ins_p.exists() and toml_p.exists()):
        return
    try:
        prompt = ins_p.read_text(encoding="utf-8")
        data = tomllib.loads(toml_p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return
    m = _NO_NET_CLAIM.search(prompt)
    if not m:
        return
    mode = (data.get("environment") or {}).get("network_mode", "public")
    if mode == "public":
        errors.append(
            f"instruction.md claims {m.group(0)!r} but [environment].network_mode "
            f"is 'public'. The prompt enforces nothing; either restrict the "
            f"environment or drop the claim — a contract that misdescribes its "
            f"own environment is worse than one that says nothing"
        )


def lint_one(root: Path, require_current_schema: bool = False) -> list[str]:
    errors: list[str] = []
    check_files(root, errors)
    check_task_toml(root, errors)
    check_executable(root, errors)
    check_harbor_native_contract(root, errors)
    check_verifier_outlasts_its_own_timeout(root, errors)
    check_evaluator_names_its_own_case(root, errors)
    check_submission_entry_is_self_contained(root, errors)
    check_enforced_limits_are_stated(root, errors)
    check_required_names_are_stated(root, errors)
    check_sampling_plane_is_derived_not_assumed(root, errors)
    check_reproduction_budget_clears_the_oracle(root, errors)
    check_network_claim_matches_reality(root, errors)
    check_output_interface_is_stated(root, errors)
    check_oracle_version_matches_the_manifest(root, errors)
    check_kpi_prose_claims_only_what_it_can_know(root, errors)
    check_no_setup_inspection_is_claimed(root, errors)
    # The bar for a case a PR ADDS, which is wider than the flag's name: five of
    # these six say nothing about `schema_version`. They share one property —
    # each would fail a large part of the historical set, where the fix is to go
    # and find out what was actually done rather than to write something that
    # satisfies a linter. Gating growth is what a check can do honestly here.
    if require_current_schema:
        check_current_schema(root, errors)
        check_solver_label_agreement(root, errors)
        check_oracle_provenance_is_recorded(root, errors)
        check_unfalsifiable_claims_are_attributed(root, errors)
        check_demand_record_is_linked(root, errors)
        check_declared_cost_is_inside_the_ceilings(root, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint sim-benchmark cases against SCHEMA.md")
    parser.add_argument(
        "case_dir", type=Path, nargs="+", help="Case directories (globs expand to a sweep)"
    )
    parser.add_argument(
        "--require-current-schema",
        action="store_true",
        help=f"also require schema_version == {CURRENT_SCHEMA_VERSION!r}; "
        "CI applies this to the cases a PR adds",
    )
    args = parser.parse_args()

    # A directory holding no task.toml is a container (cases/cfd/fluids/), not a
    # case — skip it so `cases/*/*/` sweeps do the obvious thing.
    roots = [d for d in args.case_dir if (d / "task.toml").is_file() or d.is_file()]
    skipped = [d for d in args.case_dir if d not in roots]
    for d in skipped:
        if not d.is_dir():
            print(f"error: {d} is not a directory", file=sys.stderr)
            return 2

    if not roots:
        print("error: no case directory (with a task.toml) given", file=sys.stderr)
        return 2

    failed = 0
    for root in roots:
        errors = lint_one(root, require_current_schema=args.require_current_schema)
        if errors:
            failed += 1
            print(f"FAIL ({len(errors)} issue(s)) in {root}:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
        elif len(roots) == 1:
            print(f"OK {root}")

    if len(roots) > 1:
        print(f"{len(roots)} case(s) linted: {failed} failed, {len(roots) - failed} ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
