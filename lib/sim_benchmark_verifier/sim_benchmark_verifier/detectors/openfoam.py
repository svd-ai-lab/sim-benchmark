"""OpenFOAM-specific solver-stage detector.

Reads ``log.<solver>`` files from the agent's workspace directly. Does
NOT depend on any launcher having been called — discovery is purely
artifact-based (glob for ``log.<known_of_solver>`` under
``ctx.case_dir``).

Stages emitted:

  L3_convergence   solver killed mid-run (no ``End`` marker), OR last
                   time-step's final residual exceeds RESIDUAL_TOLERANCE.
  L4_conservation  max ``|local|`` continuity error across the run
                   exceeds CONTINUITY_TOLERANCE.

When the log gives no clear L3 / L4 signal (e.g. clean completion +
small residuals + small continuity error), this detector returns
``None`` — the universal detector then handles L5 / L6 from the KPI
score fields.

Thresholds are UNVALIDATED — Phase 4 calibration sets TPR/FPR targets
and records evidence in EVIDENCE.md. Adjust before promoting. Per
ccl-evaluator's convention, an unvalidated detector still runs but its
findings should be marked with caution in downstream reports.
"""
from __future__ import annotations

import gzip
import re
import zlib
from pathlib import Path

from . import TrialContext, register


# Recognised OpenFOAM solver names. Used both for log-file globbing and
# for sniff-based applicability detection. Add new ones as we encounter
# them; reasonable but not exhaustive.
OF_SOLVERS: tuple[str, ...] = (
    "simpleFoam",
    "icoFoam",
    "pimpleFoam",
    "pisoFoam",
    "rhoSimpleFoam",
    "rhoPimpleFoam",
    "interFoam",
    "buoyantFoam",
    "buoyantSimpleFoam",
    "buoyantPimpleFoam",
    "rhoCentralFoam",
    "potentialFoam",
    "scalarTransportFoam",
    "twoPhaseEulerFoam",
    "compressibleInterFoam",
    "chtMultiRegionFoam",
)

# Thresholds — VALIDATED 2026-05-08 against real OF cavity_re100 oracle
# log + 4 mutated variants (lib/sim_benchmark_verifier/EVIDENCE.md).
# TPR=1.0, FPR=0.0 across the calibration fixture set. May need
# re-validation when extending to non-cavity OF cases.
RESIDUAL_TOLERANCE: float = 1e-2     # final residual on last step must be ≤
CONTINUITY_TOLERANCE: float = 1e-3   # max |local| continuity error must be ≤


_FINAL_RESIDUAL_RE = re.compile(
    r"Solving for ([A-Za-z_]+),\s+"
    r"Initial residual = [^,]+,\s+"
    r"Final residual = ([0-9.eE+-]+)"
)
_CONTINUITY_RE = re.compile(
    r"time step continuity errors\s*:?\s*"
    r"sum local = ([0-9.eE+-]+)"
)


# ── helpers ─────────────────────────────────────────────────────────────


def _find_of_log(case_dir: Path | None) -> Path | None:
    """Return the most-recently-modified ``log.<of_solver>`` under
    ``case_dir``, or ``None`` if nothing matches.

    Falls back to any ``log.*`` file when no solver-name match is found
    — useful when the agent renamed the log or used a non-canonical solver.
    """
    if case_dir is None or not case_dir.is_dir():
        return None
    candidates: list[Path] = []
    for solver in OF_SOLVERS:
        candidates.extend(case_dir.rglob(f"log.{solver}"))
    if not candidates:
        # Generic log.* fallback (skip log.foo.txt etc.)
        for p in case_dir.rglob("log.*"):
            if p.is_file() and "." not in p.name[len("log."):]:
                candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# Field files an OpenFOAM solver writes into a time directory.
_OF_FIELDS: tuple[str, ...] = (
    "U", "p", "p_rgh", "T", "k", "epsilon", "omega", "nut", "nuTilda",
    "phi", "alpha.water", "rho", "e", "h",
)


def _written(directory: Path, name: str) -> bool:
    """True iff OpenFOAM wrote ``name`` here, plain or gzipped.

    `writeCompression on` is an ordinary setting and NASA's published grids
    ship compressed, so `points.gz` and `U.gz` are what a perfectly normal
    case looks like on disk. Matching only the bare name reads that case as
    "nothing ran" -- measured on `bump_in_channel_2d`, whose supplied mesh is
    `constant/polyMesh/points.gz` (#211).
    """
    return (directory / name).is_file() or (directory / f"{name}.gz").is_file()


def holds_a_written_field(directory: Path) -> bool:
    """True iff this directory holds a file named as a field the solver writes.

    Public because the *stripper* has to agree with this predicate: the gate
    globs numeric directories from the submission root, so anything this
    returns true for has to be something the strip deletes, or a shipped
    solution survives somewhere the gate can still see it (#648).
    """
    return any(_written(directory, name) for name in _OF_FIELDS)


def _mesh_and_solution_present(case_dir: Path | None) -> bool:
    """True iff files with the *names* of a mesh and a solution are here.

    Names and nothing else: `_written` is `is_file()`. This is the permissive
    reading, and it is what :func:`has_solver_evidence` uses — the question
    there is "did this workspace ever see a solver?" on a tree nobody
    controlled, and a half-written `points` is still a fact about the tree.

    It is **not** admissible as a gate, and the price of using it as one was
    measured: `mkdir -p constant/polyMesh 1` plus three `printf`s putting the
    literal words `not a mesh` / `not a field` into `points`, `faces` and
    `1/U` took `lid_driven_cavity_ghia_re100` to **1.000** through this
    predicate (#361, #377). :func:`has_mesh_and_solution` is the strict form.
    """
    if case_dir is None or not case_dir.is_dir():
        return False
    has_mesh = any(
        pm.is_dir() and _written(pm, "points") and _written(pm, "faces")
        for pm in case_dir.rglob("polyMesh")
    )
    if not has_mesh:
        return False
    for d in case_dir.rglob("*"):
        if not d.is_dir():
            continue
        try:
            t = float(d.name)
        except ValueError:
            continue
        if t > 0.0 and any(_written(d, f) for f in _OF_FIELDS):
            return True
    return False


# ── the strict predicate: what OpenFOAM's own writer leaves behind ──────────
#
# The strict half reads the *serialisation format*, not the directory listing,
# and it is the same shape as `calculix._frd_holds_the_model`: every count a
# file declares about itself has to agree with what follows it. #377 priced the
# two live tracks' strict predicates against each other and they came out ~5x
# apart — calculix wanted a self-consistent FRD (a 21-line heredoc), openfoam
# wanted three files to exist (four lines of shell) — and the ruling on that
# issue is to bring openfoam to the same tier.
#
# What makes this admissible under CLAUDE.md's "The output interface" is that
# every entry it reads is part of how OpenFOAM SERIALISES a result: the class
# of the object, the count it declares, the records that follow. None of it is
# the submission's setup — not a scheme, not a boundary condition, not a
# dictionary the agent chose. A run that solved cannot fail it however it was
# configured, and a `printf` cannot satisfy it without reimplementing the
# writer.

_HEADER_SCAN_BYTES = 8192

_FOAMFILE_RE = re.compile(r"FoamFile\s*\{(.*?)\}", re.DOTALL)
# `;[ \t\r]*$` rather than `;$`: the `\r` is what a file authored on Windows
# leaves, and a header check that silently stops recognising OpenFOAM there
# would fail closed on a submission for a property of its line endings.
_HEADER_ENTRY_RE = re.compile(
    r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]+(.*?);[ \t\r]*$", re.MULTILINE)
_LIST_OPENS_RE = re.compile(rb"(\d+)\s*\(")
_INTERNAL_NONUNIFORM_RE = re.compile(
    rb"\binternalField\s+nonuniform\s+(?:List<\w+>\s*)?(\d+)\s*\(")

# `volVectorField`, `surfaceScalarField`, `pointSymmTensorField`, … — the
# classes a solver writes into a time directory. The rank is what a binary
# payload's record size is computed from.
_FIELD_CLASS_RE = re.compile(
    r"^(?:vol|surface|point)"
    r"(Scalar|Vector|SphericalTensor|SymmTensor|Tensor)Field$")
_RANK = {"Scalar": 1, "Vector": 3, "SphericalTensor": 1, "SymmTensor": 6,
         "Tensor": 9}

# What `constant/polyMesh/<name>` must say it is. `faces` has two spellings
# because the writer picks one from `writeFormat`: ASCII gets a `faceList`
# whose declared count is nFaces, binary gets a `faceCompactList` whose first
# list is nFaces+1 offsets. Both were produced and read back in the shipped
# image before this landed.
_MESH_CLASSES: dict[str, tuple[str, ...]] = {
    "points":  ("vectorField", "pointField"),
    "faces":   ("faceList", "faceCompactList"),
    "owner":   ("labelList",),
}


def _read_object(path: Path) -> bytes | None:
    """The file's bytes, transparently un-gzipped. `None` if unreadable."""
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as handle:
                return handle.read()
        return path.read_bytes()
    except (OSError, EOFError, zlib.error):
        return None


def _foam_header(buf: bytes) -> tuple[dict[str, str], int] | None:
    """Parse the `FoamFile { … }` block into its entries, plus where it ends.

    The header is ASCII even when the payload is binary — that is what makes a
    format check possible on every `writeFormat` a case may legally choose.
    """
    prefix = buf[:_HEADER_SCAN_BYTES].decode("latin-1")
    block = _FOAMFILE_RE.search(prefix)
    if block is None:
        return None
    entries = {k: v.strip() for k, v in _HEADER_ENTRY_RE.findall(block.group(1))}
    return entries, block.end()


def _word_sizes(header: dict[str, str]) -> tuple[int, int]:
    """`(label, scalar)` byte widths, read off the `arch` entry the writer
    stamped in. Defaults are OpenFOAM's own build defaults."""
    arch = header.get("arch", "")
    label = re.search(r"label=(\d+)", arch)
    scalar = re.search(r"scalar=(\d+)", arch)
    return (int(label.group(1)) // 8 if label else 4,
            int(scalar.group(1)) // 8 if scalar else 8)


def _ascii_record_counts(buf: bytes, start: int) -> tuple[int, int] | None:
    """`(groups, tokens)` seen at depth 1 of the list opening at ``start``.

    ``start`` is the index just past the `(`. A `vectorField` writes each
    record as a parenthesised group and no bare tokens; a `labelList` writes
    bare tokens and no groups; a `faceList` writes `4(1 22 463 442)`, which is
    one of each. Counting both and letting the caller accept either is what
    keeps this from having to know every type's spelling — the check is that
    the file holds as many records as it says it does, not which shape they
    took. Returns `None` if the list never closes.
    """
    groups = 0
    tokens = 0
    depth = 1
    in_token = False
    for byte in buf[start:]:
        char = chr(byte)
        if char == "(":
            if depth == 1:
                groups += 1
            depth += 1
            in_token = False
        elif char == ")":
            depth -= 1
            in_token = False
            if depth == 0:
                return groups, tokens
        elif char.isspace():
            in_token = False
        elif depth == 1:
            if not in_token:
                tokens += 1
                in_token = True
    return None


def _binary_record_bytes(class_name: str, label: int, scalar: int) -> tuple[int, ...]:
    """Possible per-record widths of a binary payload of this class.

    Empty when the class has no binary spelling this parser knows, which is
    treated as *not verified* rather than as *fine*: an unverifiable branch is
    a spelling a forgery would reach for, and OpenFOAM does not write any of
    them (binary `faces` is a `faceCompactList` of labels — measured in the
    shipped image, not assumed).
    """
    if class_name in ("labelList", "faceCompactList"):
        return (label,)
    if class_name in ("scalarField",):
        return (scalar,)
    if class_name in ("vectorField", "pointField"):
        return (3 * scalar,)
    field = _FIELD_CLASS_RE.match(class_name)
    if field:
        return (_RANK[field.group(1)] * scalar,)
    return ()


def _list_is_self_consistent(buf: bytes, header: dict[str, str], class_name: str,
                             declared: int, payload_start: int) -> bool:
    """Does the list at ``payload_start`` hold the ``declared`` records?

    ASCII counts them; binary checks that the closing `)` sits exactly where
    ``declared`` records of this class put it. This is the whole of the
    tightening — the analogue of an FRD's `2C` header count matching its ` -1`
    records — and, like that one, ccx's and OpenFOAM's own writers always
    agree with themselves, so it cannot fail a run that solved.
    """
    if declared < 1:
        return False
    if header.get("format", "ascii").strip('"') == "binary":
        for width in _binary_record_bytes(class_name, *_word_sizes(header)):
            end = payload_start + declared * width
            if buf[end:end + 1] == b")":
                return True
        return False
    counts = _ascii_record_counts(buf, payload_start)
    return counts is not None and declared in counts


def _standalone_list(path: Path, expect_object: str,
                     expect_classes: tuple[str, ...]) -> int | None:
    """Declared record count of a polyMesh object, or `None` if the file is
    not what OpenFOAM writes under that name."""
    buf = _read_object(path)
    if buf is None:
        return None
    parsed = _foam_header(buf)
    if parsed is None:
        return None
    header, end = parsed
    if header.get("object") != expect_object:
        return None
    class_name = header.get("class", "")
    if class_name not in expect_classes:
        return None
    opener = _LIST_OPENS_RE.search(buf, end)
    if opener is None:
        return None
    declared = int(opener.group(1))
    if not _list_is_self_consistent(buf, header, class_name, declared, opener.end()):
        return None
    return declared


def _resolve(directory: Path, name: str) -> Path | None:
    """`name` or `name.gz` in `directory`, whichever the writer left."""
    for candidate in (directory / name, directory / f"{name}.gz"):
        if candidate.is_file():
            return candidate
    return None


def _polymesh_is_openfoam_output(mesh: Path) -> bool:
    """True iff `points`, `faces` and `owner` read as one serialised mesh.

    The cross-file half is `nFaces`: every face has exactly one owner, so the
    face count `faces` declares and the count `owner` declares are the same
    number — offset by one in the compact spelling, because its first list is
    offsets rather than faces. `owner` is required rather than optional
    because a `polyMesh/` without it is one OpenFOAM cannot read, so demanding
    it costs no real submission anything and costs a forgery a third
    self-consistent file.
    """
    points = _resolve(mesh, "points")
    faces = _resolve(mesh, "faces")
    owner = _resolve(mesh, "owner")
    if points is None or faces is None or owner is None:
        return False
    if _standalone_list(points, "points", _MESH_CLASSES["points"]) is None:
        return False
    n_owner = _standalone_list(owner, "owner", _MESH_CLASSES["owner"])
    if n_owner is None:
        return False
    faces_header = _foam_header(_read_object(faces) or b"")
    if faces_header is None:
        return False
    faces_class = faces_header[0].get("class", "")
    declared = _standalone_list(faces, "faces", _MESH_CLASSES["faces"])
    if declared is None:
        return False
    n_faces = declared - 1 if faces_class == "faceCompactList" else declared
    return n_faces == n_owner


def _field_is_openfoam_output(path: Path, name: str) -> bool:
    """True iff `path` reads as a field OpenFOAM wrote for object `name`.

    A `vol…Field` / `surface…Field` / `point…Field` class, the object naming
    itself, the `dimensions` and `boundaryField` entries every written field
    carries, and an `internalField` that is a nonuniform list holding the
    records it declares.

    **A `uniform` internalField is not counted**, and the reason is that it is
    the one spelling with nothing to check: the writer emits it whenever every
    cell holds the same value, so a forgery gets a legal-looking field for one
    line. Refusing it costs a real run nothing, because
    :func:`_solved_time_directory` needs only one field to qualify and a solved
    time directory whose *every* field came out uniform is one where nothing
    happened.
    """
    buf = _read_object(path)
    if buf is None:
        return False
    parsed = _foam_header(buf)
    if parsed is None:
        return False
    header, end = parsed
    if header.get("object") != name:
        return False
    if not _FIELD_CLASS_RE.match(header.get("class", "")):
        return False
    if b"dimensions" not in buf or b"boundaryField" not in buf:
        return False
    nonuniform = _INTERNAL_NONUNIFORM_RE.search(buf, end)
    if nonuniform is None:
        return False
    return _list_is_self_consistent(buf, header, header["class"],
                                    int(nonuniform.group(1)), nonuniform.end())


def _solved_time_directory(directory: Path) -> bool:
    """True iff this time directory holds ≥1 field OpenFOAM wrote."""
    for name in _OF_FIELDS:
        path = _resolve(directory, name)
        if path is not None and _field_is_openfoam_output(path, name):
            return True
    return False


def _openfoam_wrote_mesh_and_solution(case_dir: Path | None) -> bool:
    """The strict predicate. A serialised mesh, plus a solved time directory.

    Same two artifacts the permissive form looks for by name, read as the
    format they are in instead.
    """
    if case_dir is None or not case_dir.is_dir():
        return False
    if not any(pm.is_dir() and _polymesh_is_openfoam_output(pm)
               for pm in case_dir.rglob("polyMesh")):
        return False
    for d in case_dir.rglob("*"):
        if not d.is_dir():
            continue
        try:
            t = float(d.name)
        except ValueError:
            continue
        if t > 0.0 and _solved_time_directory(d):
            return True
    return False


def _has_end_marker(log_text: str) -> bool:
    """OpenFOAM writes a bare ``End`` line as the final substantive
    line of a normally-terminated run."""
    tail_lines = log_text.splitlines()[-200:]
    for line in reversed(tail_lines):
        stripped = line.strip()
        if not stripped:
            continue
        return stripped == "End"
    return False


def _last_iteration_max_residual(log_text: str) -> float | None:
    """Max final residual recorded in the LAST ``Time = …`` block.

    Returns ``None`` if no residual lines were found at all (log is
    malformed or empty)."""
    blocks = re.split(r"\n(?=Time = )", log_text)
    if len(blocks) < 2:
        return None
    last_block = blocks[-1]
    residuals = [
        float(m.group(2)) for m in _FINAL_RESIDUAL_RE.finditer(last_block)
    ]
    if not residuals:
        return None
    return max(residuals)


def _max_continuity_local(log_text: str) -> float | None:
    """Max ``|local|`` continuity error reported anywhere in the run."""
    locals_ = [
        abs(float(m.group(1))) for m in _CONTINUITY_RE.finditer(log_text)
    ]
    if not locals_:
        return None
    return max(locals_)


# ── evidence check (anti-cheat) ─────────────────────────────────────────


def has_solver_evidence(ctx: TrialContext) -> bool:
    """Return True iff the workspace shows OpenFOAM actually ran — either a
    ``log.<of_solver>`` (or generic ``log.*``) file, OR (more fundamentally)
    a built mesh plus a solver-written solution time directory.

    The mesh+solution path makes the gate robust to agents that pipe solver
    stdout elsewhere instead of saving a canonical ``log.<solver>``. Real
    false-negative seen 2026-06-05: a correct lid-cavity solve left
    ``constant/polyMesh`` + a converged time dir (``U``/``p``) but no
    ``log.simpleFoam``, and was wrongly hard-zeroed despite all KPIs passing.

    **This is the permissive form, and both branches are why.** A log file is
    whatever a shell redirected into a name; ``: > log.simpleFoam`` satisfies
    it. The mesh branch here reads names and not contents, so three `printf`s
    satisfy that. Either is tolerable when the question is *"did this
    workspace ever see a solver?"* on a tree nobody controlled. Neither is
    tolerable when the answer decides a score, so an evaluator gating on
    evidence wants :func:`has_mesh_and_solution` instead.
    """
    return (
        _find_of_log(ctx.case_dir) is not None
        or _mesh_and_solution_present(ctx.case_dir)
    )


def mesh_and_solution_files_present(ctx: TrialContext) -> bool:
    """Do files with a mesh's and a solution's *names* exist? Diagnostic only.

    Published so an evaluator's ``reward_detail.json`` can tell two failures
    apart that read identically in a score: nothing was written at all, versus
    something was written that OpenFOAM did not write. Never a gate — this is
    the predicate #377 priced at four lines of shell.
    """
    return _mesh_and_solution_present(ctx.case_dir)


def has_mesh_and_solution(ctx: TrialContext) -> bool:
    """Return True iff OpenFOAM's own writer produced a mesh and a solution here.

    The strict half of :func:`has_solver_evidence`, published separately
    because an evaluator scoring off its *own* reproduction directory wants
    exactly this and not the log fallback. It is also the claim thirteen cfd
    ``kpis.json`` already make about themselves — *"a reported value with no
    ``constant/polyMesh/`` + non-zero time-dir U,p artifact scores 0"* — and
    what CLAUDE.md's detector table lists as the OpenFOAM evidence.

    It reads the serialisation and not the directory listing: see
    :func:`_polymesh_is_openfoam_output` and :func:`_field_is_openfoam_output`
    for what that means and why every entry either function looks at is part
    of the format rather than part of the submission's setup.

    What it is *not* is unforgeable — the entry point is arbitrary shell, and a
    submission willing to reimplement the writer can still satisfy it. No
    artifact predicate survives that, calculix's included; the evaluator's own
    strip-and-rerun is the defence, and this narrows the one hole rerunning
    leaves.
    """
    return _openfoam_wrote_mesh_and_solution(ctx.case_dir)


# ── detector ────────────────────────────────────────────────────────────


class OpenFOAMDetector:
    name = "openfoam"
    STAGES: tuple[str, ...] = ("L3_convergence", "L4_conservation")

    def applicable(self, ctx: TrialContext) -> bool:
        # Declarative: case opted in via task.toml.metadata.sim.solver.
        if ctx.solver_label == "openfoam":
            return True
        # Sniff: artifact glob found at least one OF log file. Robust
        # against launcher bypasses — purely based on what was
        # written to disk.
        return _find_of_log(ctx.case_dir) is not None

    def detect(self, kpi_result: dict, ctx: TrialContext) -> str | None:
        log_path = _find_of_log(ctx.case_dir)
        if log_path is None:
            return None
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        # L3 — solver was killed before normal completion. The OF "End"
        # marker is the canonical "ran to controlDict.endTime" signal.
        if not _has_end_marker(log_text):
            return "L3_convergence"

        # L3 — completed but final residuals too large to claim
        # convergence.
        max_residual = _last_iteration_max_residual(log_text)
        if max_residual is not None and max_residual > RESIDUAL_TOLERANCE:
            return "L3_convergence"

        # L4 — converged but mass conservation broken.
        max_continuity = _max_continuity_local(log_text)
        if max_continuity is not None and max_continuity > CONTINUITY_TOLERANCE:
            return "L4_conservation"

        # Log looks clean; let the universal detector decide L5 / L6 from
        # KPI scoring fields.
        return None


register(OpenFOAMDetector())
