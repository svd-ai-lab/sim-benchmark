"""Two orthogonal failure-classification axes for per-KPI scoring outcomes.

Schema reward-v3.2 splits the v3.1 single ``failure_class`` enum into two
axes per KPI, both deterministic. Inspired by ccl-evaluator's L0–L6
design (see sim-proj #125 RFC). Each axis answers a different question:

  ``provenance_stage``  Did the agent honestly record what they got?
  ``solver_stage``      How far did the simulation make it?

A KPI emits **both** labels. The cross-product is the analytical signal:

  (L6_pass,            P3_extract_mismatch)  agent simulated correctly
                                              but recorded badly →
                                              fix SKILL prompt, not model
  (L3_convergence,     P4_pass)              agent honestly reports a
                                              non-converged value →
                                              fix model's numerics
  (L2_solver_crash,    P2_extract_unrunnable) cascading; root cause is L2

Backward compatibility: this module also emits the v3.1 ``failure_class``
field on each annotated entry (mapped from the new axes) so consumers
written against reward-v3.1 keep working until they migrate.

Trial-level codes (``wall_time`` / ``turn_cap`` / ``infra``) are NOT set
here — they need the harness transcript, populated post-hoc by an
aggregator. Per-KPI axes need only the verifier's output.
"""
from __future__ import annotations


# ── Axis 1: provenance_stage ────────────────────────────────────────────
# What the verifier can determine purely from re-extracting the agent's
# claimed source. Solver-agnostic — same pipeline for every domain.
PROVENANCE_STAGES = (
    "P0_hallucination",       # claim has no source / unknown source.kind / KPI absent
    "P1_path_invalid",        # source.path missing / not absolute / file doesn't exist
    "P2_extract_unrunnable",  # source.extract command sandbox-rejected / non-zero exit / timeout
    "P3_extract_mismatch",    # extract runs, extracted value differs from claim
    "P4_pass",                # extract reproduces the claim
    "spec_error",             # case author's kpis.json is malformed (not the agent's fault)
)


# ── Axis 2: solver_stage ────────────────────────────────────────────────
# How far the simulation got before something broke. Solver-agnostic in
# concept; detector implementation is per-solver (RFC sim-proj #125).
#
# Phase 1 (this commit): we populate L5_physics / L5_quantitative / L6_pass
# from data the verifier already has. L0 / L1 / L2 / L3 / L4 require
# solver-specific detectors and remain `null` until phases 2–4 land.
SOLVER_STAGES = (
    "L0_input_syntax",        # solver input file doesn't parse (per-solver detector)
    "L1_input_semantics",     # input is not well-posed / contradicts intent
    "L2_solver_crash",        # solver started then died, OR refused to start
    "L3_convergence",         # solver ran but did not converge
    "L4_conservation",        # converged but conservation broken (where applicable)
    "L5_physics",             # value outside [physics_min, physics_max]
    "L5_quantitative",        # in the physics range but outside the tolerance band
    "L6_pass",                # nothing wrong on this axis
)


# ── v3.1 → v3.2 backward-compatibility map ──────────────────────────────
# Lets reward-v3.1 consumers keep working: the legacy ``failure_class``
# field is reduced from the (solver_stage, provenance_stage) pair via
# this table. Diagonal `(null, P4_pass)` ⇒ `null` (passed).
_LEGACY_FROM_AXES: dict[tuple, str] = {
    # provenance failures dominate — solver_stage is `null` because we
    # didn't get to the solver-axis check
    ("null", "P0_hallucination"):       "hallucination",
    ("null", "P1_path_invalid"):        "provenance_path",
    ("null", "P2_extract_unrunnable"):  "extract_runnable",
    ("null", "P3_extract_mismatch"):    "extract_format",
    ("null", "spec_error"):             "spec_error",
    # solver-side failures (provenance verified)
    ("L5_physics",      "P4_pass"):     "physics",
    ("L5_quantitative", "P4_pass"):     "convergence",
    # passed
    ("L6_pass",         "P4_pass"):     "null",
}


def _legacy_failure_class(solver: str | None, prov: str) -> str:
    """Best-effort v3.1 ``failure_class`` from the (solver, prov) pair."""
    return _LEGACY_FROM_AXES.get((solver or "null", prov), "extract_runnable")


# ── classifiers ─────────────────────────────────────────────────────────

def classify_provenance(kpi_result: dict, claim) -> str:
    """Reduce a per-KPI score dict to a ``provenance_stage`` value.

    Reads ``kpi_result["why"]`` (the verifier's free-text reason) plus
    the agent's claim shape. Returns one of ``PROVENANCE_STAGES``.
    """
    if kpi_result.get("source_verified") == 1.0:
        return "P4_pass"

    why = (kpi_result.get("why") or "").lower()

    # ── path invalid first — some "must be absolute" messages overlap
    #    with the generic "must be" hallucination check
    if "source file not found" in why or "could not be read" in why:
        return "P1_path_invalid"
    if "must be absolute" in why:
        return "P1_path_invalid"

    # ── hallucination — claim shape problems
    if claim is None or "kpi absent from result.json" in why:
        return "P0_hallucination"
    if "kpi claim must be an object" in why or "missing source field" in why:
        return "P0_hallucination"
    if "unknown source.kind" in why:
        return "P0_hallucination"
    if "extracted not numeric" in why or "extractor returned no number" in why:
        return "P0_hallucination"
    if " must be " in why and "source." in why:
        return "P0_hallucination"
    if "source.path missing" in why or "source.path may not contain" in why:
        return "P0_hallucination"

    # ── extract pipeline runnability
    if "extractor rejected" in why:
        return "P2_extract_unrunnable"
    if "extractor exited" in why or "extractor timed out" in why:
        return "P2_extract_unrunnable"

    # ── extract value vs claim
    if "value mismatch" in why or "differs from extracted" in why:
        return "P3_extract_mismatch"
    if "extracted value" in why and ("mismatch" in why or "differs" in why):
        return "P3_extract_mismatch"

    # ── case author errors (verifier rejected the spec, not the agent's fault)
    if "must be 'completed', 'measure', or 'step_param'" in why:
        return "spec_error"

    # Catch-all when source_verified=0 but no familiar reason — most
    # remaining cases are extract-pipeline failures we haven't enumerated.
    return "P2_extract_unrunnable"


def classify_solver_stage(
    kpi_result: dict,
    sim_records: list[dict] | None = None,
    case_dir=None,
    solver_label: str | None = None,
) -> str | None:
    """Reduce a per-KPI score dict to a ``solver_stage`` value, or ``None``.

    Phase 3a routes through the ``detectors/`` plugin layer. Each
    registered ``SolverStageDetector`` (universal + any solver-specific
    extensions) gets a chance to attribute; first non-None wins.

    Phase 1 stages (L5_physics / L5_quantitative / L6_pass) and Phase 2
    (L2_solver_crash from sim_records) live in ``detectors/universal.py``.
    Phase 3b will add ``detectors/openfoam.py`` for L3 / L4.

    ``None`` is returned when no detector can attribute — we don't
    fabricate a stage we can't verify.
    """
    from .detectors import TrialContext, dispatch

    ctx = TrialContext(
        sim_records=sim_records or [],
        case_dir=case_dir,
        solver_label=solver_label,
    )
    return dispatch(kpi_result, ctx)


def annotate_per_kpi(
    per_kpi: dict,
    result_obj: dict,
    sim_records: list[dict] | None = None,
    case_dir=None,
    solver_label: str | None = None,
) -> dict:
    """Mutate ``per_kpi`` in place: add ``solver_stage`` /
    ``provenance_stage`` / ``failure_class`` (legacy) fields to each
    entry. Return a small summary dict with both-axis distributions.

    Routes solver-stage classification through the ``detectors/`` plugin
    layer. The trial-context arguments —
    ``sim_records`` (from ``meta_detail.records``), ``case_dir`` (the
    agent's workspace), ``solver_label`` (from
    ``task.toml.metadata.sim.solver``) — are bundled into a
    ``TrialContext`` and passed to every applicable detector.
    """
    from .detectors import TrialContext, all_known_stages, dispatch

    ctx = TrialContext(
        sim_records=sim_records or [],
        case_dir=case_dir,
        solver_label=solver_label,
    )

    # Counts dict keys = union of every registered detector's STAGES,
    # plus "null" for KPIs no detector could attribute. Open enum →
    # adding a new detector automatically extends this.
    solver_counts: dict[str, int] = {s: 0 for s in all_known_stages()}
    solver_counts["null"] = 0
    prov_counts: dict[str, int] = {p: 0 for p in PROVENANCE_STAGES}

    for name, kpi_result in per_kpi.items():
        prov = classify_provenance(kpi_result, result_obj.get(name))
        solver = dispatch(kpi_result, ctx)
        kpi_result["provenance_stage"] = prov
        kpi_result["solver_stage"] = solver
        # v3.1 backward-compat field
        kpi_result["failure_class"] = _legacy_failure_class(solver, prov)

        prov_counts[prov] = prov_counts.get(prov, 0) + 1
        bucket = solver if solver is not None else "null"
        solver_counts[bucket] = solver_counts.get(bucket, 0) + 1

    return {
        "solver_stage_counts":     solver_counts,
        "provenance_stage_counts": prov_counts,
    }


# ── v3.1 compatibility shims (still exported, so existing imports work) ──

# v3.1 callers expected `PER_KPI_CLASSES` and `classify_kpi`. Keep both;
# new code should use classify_provenance / classify_solver_stage.
PER_KPI_CLASSES = (
    "null",
    "physics",
    "convergence",
    "provenance_path",
    "extract_runnable",
    "extract_format",
    "hallucination",
    "spec_error",
)


def classify_kpi(kpi_result: dict, claim, sim_records: list[dict] | None = None) -> str:
    """Deprecated v3.1 name — returns the legacy ``failure_class``.

    Prefer ``classify_provenance`` + ``classify_solver_stage``. This
    wrapper exists to keep existing tests + downstream tools working
    until they migrate.
    """
    prov = classify_provenance(kpi_result, claim)
    solver = classify_solver_stage(kpi_result, sim_records)
    return _legacy_failure_class(solver, prov)
