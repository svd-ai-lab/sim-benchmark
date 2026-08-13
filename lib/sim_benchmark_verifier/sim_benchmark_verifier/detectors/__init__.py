"""Plugin detector framework for solver-stage classification.

Each detector contributes stages from its own ``STAGES`` set; their union forms
the open ``solver_stage`` enum. Detectors are dispatched in registration order;
the first non-``None`` stage wins.

Conventions:
- Solver-specific detectors register **before** ``universal`` so they
  get first crack at attributing solver-specific failures (L3 / L4 /
  domain stages). The universal detector is the fallback layer for
  L0 / L2 / L5 / L6.
- Each detector reads artifacts directly (``log.<solver>``, ``*.cas``,
  …); it must NOT depend on optional legacy run records. ``sim_records``
  is a corroborating signal, not a primary one.
- Detectors are pure: same input → same output. No LLM judgment in
  this layer (per ccl-evaluator pattern, soft judgment lives in a
  separate optional review layer).

Adding a new solver:
    1. Drop a file in ``detectors/<solver>.py``
    2. Define a class with ``name`` / ``STAGES`` / ``applicable`` /
       ``detect``
    3. Call ``register(MyDetector())`` at module bottom
    4. Add ``from . import <solver>`` at the bottom of this file
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class TrialContext:
    """Aggregates everything detectors might need to inspect a trial.

    Built by the verifier at scoring time. Detectors should treat it as
    read-only.
    """
    sim_records: list[dict] = field(default_factory=list)
    case_dir: Path | None = None        # workspace where agent left artifacts
    solver_label: str | None = None     # task.toml.metadata.sim.solver


@runtime_checkable
class SolverStageDetector(Protocol):
    """Pluggable detector for solver-stage classification."""

    name: str
    STAGES: tuple[str, ...]

    def applicable(self, ctx: TrialContext) -> bool:
        """Return True iff this detector should run on this trial."""
        ...

    def detect(self, kpi_result: dict, ctx: TrialContext) -> str | None:
        """Return one of ``self.STAGES`` (or ``None`` if can't attribute)."""
        ...


# ── Registry ─────────────────────────────────────────────────────────────

_REGISTRY: list[SolverStageDetector] = []


def register(detector: SolverStageDetector) -> None:
    """Register a detector. Earlier registrations get higher dispatch
    priority — solver-specific detectors should register before the
    universal fallback.
    """
    _REGISTRY.append(detector)


def registered() -> tuple[SolverStageDetector, ...]:
    """Snapshot of the registry — primarily for tests."""
    return tuple(_REGISTRY)


def all_known_stages() -> set[str]:
    """Union of every registered detector's STAGES. The full open enum
    at runtime — count-dict initialisers + leaderboard tooling read this.
    """
    stages: set[str] = set()
    for d in _REGISTRY:
        stages.update(d.STAGES)
    return stages


def dispatch(kpi_result: dict, ctx: TrialContext) -> str | None:
    """Run applicable detectors in registration order; return the first
    non-``None`` stage. Returns ``None`` when nothing attributed.
    """
    for d in _REGISTRY:
        if not d.applicable(ctx):
            continue
        stage = d.detect(kpi_result, ctx)
        if stage is not None:
            return stage
    return None


# ── Auto-register builtin detectors ──────────────────────────────────────
# Solver-specific detectors first (so they get first crack at attributing),
# universal detector last (fallback for L0/L2/L5/L6).
from . import openfoam   # noqa: F401, E402
from . import ltspice    # noqa: F401, E402
from . import comsol     # noqa: F401, E402
from . import simulink   # noqa: F401, E402
from . import verilator  # noqa: F401, E402
from . import calculix   # noqa: F401, E402
from . import ngspice    # noqa: F401, E402
from . import cadquery   # noqa: F401, E402
from . import mujoco     # noqa: F401, E402
from . import cantera    # noqa: F401, E402
from . import pybamm     # noqa: F401, E402
from . import iverilog   # noqa: F401, E402
from . import openroad   # noqa: F401, E402
from . import universal  # noqa: F401, E402


# ── Cross-solver evidence check (anti-cheat) ─────────────────────────────

def has_solver_evidence(ctx: TrialContext) -> bool:
    """Return True iff there's artifact evidence the declared solver ran.

    Used by ``score.py`` to hard-zero analytical-shortcut trials: the
    agent who hand-calculates a value and writes it to ``kpis.txt`` will
    pass per-KPI provenance (file exists, extract works) but fails this
    check because no solver output is on disk.

    **Where this actually runs, measured (#196).** ``score.py`` is called by
    **no** live case: all 127 go through ``native_cantera`` (50),
    ``native_pybamm`` (50), ``openfoam_interface`` (19) or
    ``calculix_interface`` (8), and only the last of those calls this
    function. So the live reach of every detector below is the 8 packaging
    cases. That is not the same as the check being weak — it is called there
    on the **evaluator's own re-run directory**, which is the one place an
    artifact check is not forgeable — but it does mean every other live track
    is defended by strip-and-re-run rather than by anything here. (Counting
    the others here rather than saying "the other three" is deliberate: a
    track list that is typed out is a track that can be silently dropped, and
    the count went stale the moment a fourth track landed — #305.)

    Dispatch is solver-label-driven: each solver-specific detector
    exposes its own ``has_solver_evidence(ctx)``. Unknown / missing
    solver_label → returns True (no gate; preserves legacy behaviour).

    **Cross-domain (multi-solver) cases**: a ``solver`` label may name more
    than one solver, joined by ``+`` or ``,`` (e.g. ``"cadquery+calculix"``
    for a geometry→mesh→FEM chain). Evidence is then required from **every**
    named (known) solver — the agent must show artifacts from each stage of
    the chain, not just the last one. Unknown sub-labels don't gate.
    """
    if ctx.solver_label is None:
        return True
    labels = [s.strip() for s in ctx.solver_label.replace(",", "+").split("+") if s.strip()]
    for label in labels:
        fn = _EVIDENCE_FUNCS.get(label)
        if fn is None:
            continue  # unknown sub-label — don't gate on it
        sub_ctx = TrialContext(
            sim_records=ctx.sim_records, case_dir=ctx.case_dir, solver_label=label,
        )
        if not fn(sub_ctx):
            return False
    return True


# solver label → its has_solver_evidence. Lets has_solver_evidence require
# evidence from every solver in a cross-domain chain ("cadquery+calculix").
_EVIDENCE_FUNCS = {
    "comsol":    comsol.has_solver_evidence,
    "openfoam":  openfoam.has_solver_evidence,
    "ltspice":   ltspice.has_solver_evidence,
    "simulink":  simulink.has_solver_evidence,
    "verilator": verilator.has_solver_evidence,
    "calculix":  calculix.has_solver_evidence,
    "ngspice":   ngspice.has_solver_evidence,
    "cadquery":  cadquery.has_solver_evidence,
    "mujoco":    mujoco.has_solver_evidence,
    "cantera":   cantera.has_solver_evidence,
    "pybamm":    pybamm.has_solver_evidence,
    "iverilog":  iverilog.has_solver_evidence,
    "openroad":  openroad.has_solver_evidence,
}


# ── Study (optimization / sensitivity) evidence ──────────────────────────
# "Study" task types (optimization, sensitivity) require the solver to be run
# MANY times over a parameter space — not once. The single-shot
# ``has_solver_evidence`` proves the solver ran at all; this proves a *study*
# happened: either a design/sample table (a CSV/TSV/DAT of >= n_evals_min
# numeric rows mapping parameters -> outputs — the natural artifact of a
# sweep/optimisation) OR >= n_evals_min legacy run records. The caller still
# enforces ``has_solver_evidence`` separately, so a faked table with no solver
# output cannot pass.

import re as _re


def _numeric_table_rows(path: Path) -> int:
    """Count rows with >= 2 numeric columns (a design/sample table)."""
    num = _re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
    rows = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _i, line in enumerate(fh):
                if _i > 100000:
                    break
                toks = [t for t in _re.split(r"[,\s;\t]+", line.strip()) if t]
                if sum(1 for t in toks if num.fullmatch(t)) >= 2:
                    rows += 1
    except OSError:
        return 0
    return rows


def has_study_evidence(
    ctx: TrialContext, n_evals_min: int, design_table: str | None = None,
) -> bool:
    """True iff there's evidence the solver was driven >= n_evals_min times
    over a parameter space.

    Primary signal is the agent's **design table** — the sweep summary the
    case declares (``study.design_table``, e.g. ``samples.csv``). We count its
    numeric rows. If the case names no table, fall back to scanning ``.csv`` /
    ``.tsv`` files only (NOT ``.dat`` / ``.txt`` — those are solver output, and
    a single CalculiX ``.dat`` eigenvalue table would otherwise masquerade as a
    multi-eval sweep). Either way, >= n_evals_min legacy run records also
    satisfies it. The caller separately requires ``has_solver_evidence``, so a
    table with no real solver output on disk still fails the overall gate.
    """
    if n_evals_min <= 1:
        return True
    cd = ctx.case_dir
    if cd is not None and cd.is_dir():
        if design_table:
            base = design_table.rsplit("/", 1)[-1].lower()
            for p in cd.rglob("*"):
                if p.is_file() and p.name.lower() == base and _numeric_table_rows(p) >= n_evals_min:
                    return True
        else:
            for p in cd.rglob("*"):
                if p.is_file() and p.name.lower().endswith((".csv", ".tsv")) \
                        and _numeric_table_rows(p) >= n_evals_min:
                    return True
    return len(ctx.sim_records) >= n_evals_min
