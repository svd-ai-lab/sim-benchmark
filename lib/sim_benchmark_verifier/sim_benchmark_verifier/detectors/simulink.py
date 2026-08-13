"""Simulink-specific solver-stage detector + evidence check.

Same shape as ``comsol.py`` and ``openfoam.py``:

1. **Solver-stage detection** — scan agent workspace for Simulink
   artifacts and emit ``L2_solver_crash`` when nothing real ran.

2. **Evidence check** (anti-cheat) — ``has_solver_evidence(ctx)`` lets
   ``score.py`` hard-zero analytical-shortcut trials. A 2nd-order
   linear plant has a tempting closed-form (``omega_n = sqrt(k/m)``);
   an agent that fills `result.json` from formulas without ever
   running ``sim()`` will trip this gate.

Evidence taxonomy:
    Primary   — ``*.slx`` file with size > 1 KB (Simulink model archive
                — a zipped XML bundle, near-impossible to hand-fake).
                A skeleton ``new_system + save_system`` empty model is
                already > 1 KB.
    Secondary — ``*.mat`` file with size > 256 B (saved simulation
                output: timeseries / Dataset / SimulationOutput dump).
                256 B excludes near-empty placeholder ``.mat`` headers.
    Tertiary  — ``*.log`` / ``*.txt`` containing the canonical Simulink
                banner (``Simulink``, ``MATLAB``, ``sim('...')``,
                ``Simulink.SimulationOutput``) in the first ~32 KB.
    Quaternary — legacy ``.sim/runs/<run_id>/`` record (if a future
                ``simulink`` driver lands).

Stage emitted:
    ``L2_solver_crash`` — no evidence at all OR evidence present but
                          banner indicates a fatal error
                          (``Error using sim``, license failure, etc.).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import TrialContext, register


# Simulink/MATLAB banner regexes. Match the canonical signatures
# that ``matlab -batch`` and ``sim()`` write to stdout/stderr/log.
_SIMULINK_BANNER_RE = re.compile(
    r"(?i)(?:"
    r"\bSimulink\.SimulationOutput\b"
    r"|\bSimulink\s+model\b"
    r"|\bsim\(['\"]"
    r"|\bnew_system\(['\"]"
    r"|\bload_system\(['\"]"
    r"|\bset_param\(['\"]"
    r"|\bMATLAB\s+version\b"
    r"|\bMathWorks\b"
    r"|\bode(?:45|23|15s|113)\b"          # default ODE solvers — show up in elapsed traces
    r"|\bStopTime\b.*\bSolver\b"
    r")"
)

# Fatal-error markers — Simulink/MATLAB ran but died. Forces L2 even
# when the .slx file exists (e.g. agent built an invalid model).
_SIMULINK_FATAL_RE = re.compile(
    r"(?i)(?:"
    r"Error\s+using\s+sim\b"
    r"|License\s+(?:checkout|error|not\s+available)"
    r"|Cannot\s+find\s+Simulink\s+license"
    r"|Out\s+of\s+memory"
    r"|Algebraic\s+loop\s+detected"        # common Simulink failure
    r"|Singular\s+Jacobian"
    r"|Index\s+exceeds\s+(?:matrix|array)\s+dimensions"
    r"|Subscript\s+indices\s+must\s+(?:be|either)"
    r"|Failed\s+to\s+solve\s+algebraic\s+loop"
    r"|Java\s+exception\s+occurred"
    r"|Block\s+'.+'\s+is\s+not\s+a\s+valid\s+block"
    r"|Invalid\s+Simulink\s+object\s+name"
    r")"
)


# Size thresholds — empirical. A trivial new_system + save_system .slx
# is ~3-5 KB on R2024b (it's a zipped XML bundle with manifest +
# model.xml + a couple block stubs). 1 KB is a safe lower bound.
_SLX_MIN_BYTES = 1024
# A near-empty saved .mat has header overhead ~200 B; legitimate
# simulation output (even a 10-step ode45 timeseries) is > 1 KB.
# 256 B excludes header-only .mat files.
_MAT_MIN_BYTES = 256


def _scan_for_artifacts(case_dir: Path) -> dict:
    """Walk ``case_dir`` for Simulink evidence. Returns:

        slx_files       — list of *.slx paths with size > _SLX_MIN_BYTES
        mat_files       — list of *.mat paths with size > _MAT_MIN_BYTES
        simulink_logs   — list of log/txt paths whose head matches
                          Simulink banner regex
        sim_run_dirs    — list of legacy .sim/runs/<run_id> dirs
    """
    if case_dir is None or not case_dir.is_dir():
        return {
            "slx_files": [], "mat_files": [],
            "simulink_logs": [], "sim_run_dirs": [],
        }

    slx_files: list[Path] = []
    mat_files: list[Path] = []
    simulink_logs: list[Path] = []

    for p in case_dir.rglob("*"):
        if not p.is_file():
            continue
        name_lower = p.name.lower()

        # Primary — .slx file with non-trivial size.
        if name_lower.endswith(".slx"):
            try:
                if p.stat().st_size >= _SLX_MIN_BYTES:
                    slx_files.append(p)
            except OSError:
                pass
            continue

        # Secondary — .mat file with non-trivial size.
        if name_lower.endswith(".mat"):
            try:
                if p.stat().st_size >= _MAT_MIN_BYTES:
                    mat_files.append(p)
            except OSError:
                pass
            continue

        # Tertiary — log/txt with Simulink banner. Skip claude.log,
        # python tracebacks, etc. by content-sniffing first 32 KB.
        if name_lower.endswith((".log", ".txt", ".out")):
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:32 * 1024]
            except OSError:
                continue
            if _SIMULINK_BANNER_RE.search(head):
                simulink_logs.append(p)

    # Quaternary — legacy .sim/runs/<run_id>/ dirs.
    sim_run_dirs: list[Path] = []
    runs_root = case_dir / ".sim" / "runs"
    if runs_root.is_dir():
        for run in runs_root.iterdir():
            if run.is_dir():
                sim_run_dirs.append(run)

    return {
        "slx_files":     slx_files,
        "mat_files":     mat_files,
        "simulink_logs": simulink_logs,
        "sim_run_dirs":  sim_run_dirs,
    }


def has_solver_evidence(ctx: TrialContext) -> bool:
    """Return True iff there's artifact evidence Simulink was actually
    run. Used by ``score.py`` to hard-zero analytical shortcuts.

    The most common shortcut Simulink agents try: fill ``result.json``
    with KPIs computed straight from ``omega_n = sqrt(k/m)`` and skip
    ``sim()`` entirely. Without a .slx, .mat, or simulink banner on
    disk this returns False and the trial gets zero.
    """
    if ctx.case_dir is None:
        return False
    artifacts = _scan_for_artifacts(ctx.case_dir)
    return bool(
        artifacts["slx_files"]
        or artifacts["mat_files"]
        or artifacts["simulink_logs"]
        or artifacts["sim_run_dirs"]
    )


def _has_fatal_error(simulink_logs: list[Path]) -> bool:
    """Check the most-recently-modified Simulink log for a fatal marker."""
    if not simulink_logs:
        return False
    log = max(simulink_logs, key=lambda p: p.stat().st_mtime)
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(_SIMULINK_FATAL_RE.search(text))


class SimulinkDetector:
    name = "simulink"
    STAGES: tuple[str, ...] = ("L2_solver_crash",)

    def applicable(self, ctx: TrialContext) -> bool:
        if ctx.solver_label == "simulink":
            return True
        if ctx.case_dir is None:
            return False
        return has_solver_evidence(ctx)

    def detect(self, kpi_result: dict, ctx: TrialContext) -> str | None:
        if ctx.case_dir is None:
            return None
        artifacts = _scan_for_artifacts(ctx.case_dir)

        # No artifacts → analytical-shortcut OR runner never invoked
        # MATLAB. Either way, no Simulink run → L2.
        if not (artifacts["slx_files"] or artifacts["mat_files"]
                or artifacts["simulink_logs"] or artifacts["sim_run_dirs"]):
            return "L2_solver_crash"

        # Artifacts present but log shows fatal error → L2.
        if _has_fatal_error(artifacts["simulink_logs"]):
            return "L2_solver_crash"

        # Artifact-clean — let universal decide L5 / L6.
        return None


register(SimulinkDetector())
