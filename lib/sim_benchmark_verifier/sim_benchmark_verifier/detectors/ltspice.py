"""LTspice solver-stage detector.

The universal stages (``L2_solver_crash`` / ``L3_convergence`` / ``L5_*`` /
``L6_pass``) cover
LTspice's failure space semantically. There is no domain-specific
stage worth adding — DC operating-point failure rolls into L2; trapezoidal
timestep collapse rolls into L3. Hence ``STAGES = ()``.

What this detector adds is an **artifact-based detection path** for
those universal stages — the universal detector can only see
``sim_records`` (which is empty when the agent invoked LTspice
directly via wine), so it misses crashes / convergence failures from
bypass runs. This detector reads the ``.log`` file directly.

LTspice failure-mode survey:

  Pre-run:
    "*** Cannot find" / "Could not load .cir"     → L2_solver_crash
    Missing model / subckt definitions            → L2_solver_crash
  Run-time:
    "Singular matrix"                             → L2_solver_crash
    "DC convergence failed" / "GMIN stepping"     → L2_solver_crash
    "Time step too small" / "convergence failed"  → L3_convergence
    "trapezoidal truncation" / "gear iteration"   → L3_convergence
  Post-run:
    "Total elapsed time" + no error markers       → None (universal decides)

Encoding: LTspice writes UTF-16-LE logs (with optional BOM); we mirror
the encoding handling from ``provenance._read_ltspice_log``.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import TrialContext, register


# Markers indicating the run hit a hard error / abort / missing-model
# situation — all roll into L2_solver_crash.
_CRASH_RE = re.compile(
    r"(?im)^\s*(?:"
    r"\*\*\*"                                 # LTspice's *** error prefix
    r"|error\b"
    r"|aborted\b"
    r"|cannot find"
    r"|could not load"
    r"|singular matrix"
    r"|dc (?:operating point|convergence) (?:failed|did not converge)"
    r"|gmin stepping (?:failed|did not converge)"
    r"|source stepping (?:failed|did not converge)"
    r")"
)

# Markers for run-time numerical convergence collapse — L3_convergence.
_CONVERGENCE_RE = re.compile(
    r"(?i)(?:"
    r"time step too small"
    r"|convergence (?:failed|aborted)"
    r"|timestep\s+truncation\s+failed"
    r"|gear\s+iteration\s+failed"
    r"|internal\s+timestep\s+too\s+small"
    r")"
)

# LTspice-log fingerprints — distinguishes LTspice logs from other tools'
# .log files when applicability is sniff-based (no solver_label declared).
_LTSPICE_FINGERPRINT_RE = re.compile(
    r"(?i)(?:"
    r"circuit:\s+\*"          # circuit header line
    r"|\.tran\b|\.ac\b|\.dc\b|\.noise\b|\.tf\b"  # analysis directives echoed
    r"|total elapsed time\s*:"
    r"|gmin stepping"
    r")"
)


def _read_log_handling_utf16(path: Path) -> str:
    """Mirror ``provenance._read_ltspice_log`` — LTspice writes UTF-16-LE."""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace").replace("\x00", "")
    if data.count(b"\x00") > max(1, len(data) // 10):
        return data.decode("utf-16-le", errors="replace").replace("\x00", "")
    try:
        return data.decode("utf-8").replace("\x00", "")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace").replace("\x00", "")


def _find_ltspice_log(case_dir: Path | None) -> Path | None:
    """Most-recently-modified ``*.log`` under ``case_dir`` that does not
    look like an OF log (``log.<solver>`` naming).

    Returns ``None`` if nothing matches. Doesn't fingerprint here —
    the caller uses :func:`_is_ltspice_log` for that when needed.
    """
    if case_dir is None or not case_dir.is_dir():
        return None
    candidates = [
        p for p in case_dir.rglob("*.log")
        if p.is_file() and not p.name.startswith("log.")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _is_ltspice_log(text: str) -> bool:
    return bool(_LTSPICE_FINGERPRINT_RE.search(text))


def has_solver_evidence(ctx: TrialContext) -> bool:
    """Return True iff there's an LTspice ``.log`` (fingerprint-matched)
    in the agent's workspace. Used by score.py's hard-zero anti-cheat
    gate to reject analytical-shortcut trials that hand-calculated KPIs
    without invoking LTspice.
    """
    return _find_ltspice_log(ctx.case_dir) is not None


class LTspiceDetector:
    name = "ltspice"
    # Survey conclusion: universal stages are sufficient. This detector
    # contributes detection coverage, not new stage values.
    STAGES: tuple[str, ...] = ()

    def applicable(self, ctx: TrialContext) -> bool:
        if ctx.solver_label == "ltspice":
            return True
        log = _find_ltspice_log(ctx.case_dir)
        if log is None:
            return False
        return _is_ltspice_log(_read_log_handling_utf16(log))

    def detect(self, kpi_result: dict, ctx: TrialContext) -> str | None:
        log = _find_ltspice_log(ctx.case_dir)
        if log is None:
            return None
        text = _read_log_handling_utf16(log)

        # L3 first — convergence failures are more specific than generic
        # error markers and may co-occur with them (LTspice often prints
        # "*** Convergence failed..."). Catching L3 here avoids a false
        # L2 attribution from the *** prefix.
        if _CONVERGENCE_RE.search(text):
            return "L3_convergence"
        if _CRASH_RE.search(text):
            return "L2_solver_crash"
        return None


register(LTspiceDetector())
