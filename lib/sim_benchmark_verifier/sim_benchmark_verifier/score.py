"""Shared KPI scoring helpers and the legacy generic evaluator.

The public HWE-bench tasks use their track-specific clean-rerun evaluators.
Those evaluators import the tolerance and physics helpers from this module.

The generic CLI below is retained for archived task contracts and offline
rescoring. It is not used by the 68 public tasks. Its optional historical run
metadata has zero weight in ``final_score``; current tasks derive their score
only from evaluator-owned reproduction and KPI checks.

Layer 1 — meta (DIAGNOSTIC ONLY, weight = 0):
    meta_score = 0.5 · exec_ok + 0.5 · process_ok
    exec_ok    = at least one `kind=run` record in `sim --json logs`
    process_ok = at least one of those has ok=True
    (Retained as diagnostic compatibility data for archived task contracts.)

Layer 2 — kpi (group-weighted accuracy, weight = 1.0):
    Each KPI in kpis.json belongs to a `group`; group weights sum to 1.
    Per-KPI score = source_verified · physics_pass · band_pass
        source_verified — agent's claim survives a re-extract from the
                          declared source (file / sim run stdout / sim
                          run parsed_output)
        physics_pass    — predicted value lies in [physics_min, physics_max]
        band_pass       — BINARY: |pred − gt_value| ≤ pass_tol → 1, else 0.
                          No partial credit; see `band_score`.
    Group score = mean of member KPI scores. Missing KPI in result.json
    contributes 0 (not "exclude").
    kpi_score = sum(group.weight · group.score) over groups.

Reward layout:
    reward.json (Harbor contract — ONE key):
        {"score": <final_score>}
    reward_detail.json (everything):
        schema_version, meta_score + breakdown, kpi_score + per-group +
        per-KPI breakdown (value, kpi_score, source_verified, physics_pass,
        band_pass, absolute_error, why), final_score, runtime_detail.

KPI groups + provenance both live in `tests/kpis.json` (same Harbor mount).
result.json is agent-written at /tmp/agent/result.json.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────
# Layer 1 — meta gate
# ──────────────────────────────────────────────────────────────────────

def _query_sim_history() -> list[dict]:
    """Query optional legacy run history via its historical CLI interface.

    Raises ``RuntimeError`` when the legacy interface is unavailable or its
    output is unparseable. Current public tasks do not call this function.

    Older versions accepted a ``--all`` flag; later versions returned the full
    list when ``logs`` was invoked without a positional target.
    """
    try:
        result = subprocess.run(
            ["sim", "--json", "logs"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=10, check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"legacy run-history CLI not on PATH: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("sim --json logs timed out (>10s)") from e
    if result.returncode != 0:
        raise RuntimeError(
            f"sim --json logs exited {result.returncode}: "
            f"{result.stderr.strip()[:200]}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"sim --json logs output not JSON: {e}") from e


def meta_check(_query=None) -> tuple[float, dict]:
    """Return (meta_score, detail).

    meta_score = 0.5 · exec_ok + 0.5 · process_ok
    """
    if _query is None:
        _query = _query_sim_history
    try:
        records = _query()
    except RuntimeError as e:
        return 0.0, {
            "exec_ok": 0.0, "process_ok": 0.0,
            "n_runs": 0, "n_ok_runs": 0,
            "why": str(e),
        }
    # Older records carried `kind: "run"` (vs e.g. "session"); the later
    # schema dropped `kind` because `run` was the only record type. Accept
    # either: explicit kind=="run" OR
    # missing kind (new schema). Same for ok-ness: older used `ok: True`,
    # current uses `exit_code: 0`.
    runs = [r for r in records if r.get("kind", "run") == "run"]
    ok_runs = [
        r for r in runs
        if r.get("ok") is True or r.get("exit_code") == 0
    ]
    exec_ok    = 1.0 if runs else 0.0
    process_ok = 1.0 if ok_runs else 0.0
    detail = {
        "exec_ok":    exec_ok,
        "process_ok": process_ok,
        "n_runs":     len(runs),
        "n_ok_runs":  len(ok_runs),
        # Solver wall — sim records each run's duration_ms; cost_meter reads
        # this to populate compute.solver_wall_seconds (vs agent.wall_seconds
        # which includes LLM thinking + tool latency). Per-run map kept for
        # finer "which sim run was the expensive one" attribution.
        "total_solver_ms":     sum(int(r.get("duration_ms") or 0) for r in runs),
        "per_run_duration_ms": {str(r.get("run_id")): r.get("duration_ms") for r in runs},
        "solvers":    sorted({r.get("solver") for r in runs if r.get("solver")}),
        "records":    runs,  # carried for KPI provenance lookups
    }
    if not runs:
        detail["why"] = "no legacy run-history records"
    elif not ok_runs:
        detail["why"] = "no run record has ok=True"
    return 0.5 * exec_ok + 0.5 * process_ok, detail


# ──────────────────────────────────────────────────────────────────────
# Layer 2 — KPI scoring
# ──────────────────────────────────────────────────────────────────────

# ── the tolerance band ────────────────────────────────────────────────
# `pass_tol` is an ABSOLUTE tolerance in the KPI's own unit: the value
# passes when |pred − gt_value| ≤ pass_tol. `gross_error_tol` is the
# same shape but is NOT part of the score — it only separates "wrong"
# from "wildly wrong" for failure attribution.
#
# `T_good` / `T_bad` are the historical spellings of the same two
# fields. They are still read so that a stored trial and a case that
# has not been migrated keep scoring; new cases may not use them
# (tools/lint_case.py rejects them) and the fallback goes away one
# release after the rename.

def pass_tol(spec: dict) -> float:
    """The absolute tolerance a value must land inside to score."""
    if spec.get("pass_tol") is not None:
        return float(spec["pass_tol"])
    return float(spec["T_good"])


def gross_error_tol(spec: dict) -> float | None:
    """Diagnostic-only threshold separating "wrong" from "wildly wrong"."""
    for key in ("gross_error_tol", "T_bad"):
        if spec.get(key) is not None:
            return float(spec[key])
    return None


def band_score(err: float, tol: float) -> float:
    """Binary: inside the tolerance band scores 1, outside scores 0.

    There is deliberately no partial credit. This scores whether a
    computed result is right, and a number that is wrong does not become
    less wrong by being wrong by less — an ignition delay 10% off means
    the mechanism, the reactor form or the peak extraction was wrong,
    which is a wrong answer and not a near miss.

    Density for anyone doing reward shaping comes from the continuous
    `absolute_error` that every evaluator still writes into
    reward_detail.json, and from the multi-KPI / process-gate structure
    around this function — never from decay inside one KPI.
    """
    return 1.0 if err <= tol else 0.0


def _physics_pass(spec: dict, pred: float) -> tuple[float, str]:
    if not math.isfinite(pred):
        return 0.0, f"pred {pred} is not finite"
    pmin = spec.get("physics_min")
    pmax = spec.get("physics_max")
    if pmin is not None and pred < float(pmin):
        return 0.0, f"pred {pred} < physics_min {pmin}"
    if pmax is not None and pred > float(pmax):
        return 0.0, f"pred {pred} > physics_max {pmax}"
    return 1.0, "ok"


def _band_pass(spec: dict, pred: float) -> float:
    return band_score(abs(pred - float(spec["gt_value"])), pass_tol(spec))


def _score_one_kpi(name: str, spec: dict, claim, sim_records: list[dict]) -> dict:
    """Per-KPI score = source_verified · physics_pass · band_pass (each 0–1)."""
    from .provenance import verify_source

    if claim is None:
        return {
            "kpi_score":       0.0,
            "source_verified": 0.0,
            "physics_pass":    0.0,
            "band_pass":       0.0,
            "why":             "KPI absent from result.json",
        }
    pv = verify_source(claim, sim_records)
    if not pv.ok:
        return {
            "kpi_score":       0.0,
            "source_verified": 0.0,
            "physics_pass":    0.0,
            "band_pass":       0.0,
            "value":           claim.get("value") if isinstance(claim, dict) else None,
            "why":             f"source verification failed: {pv.why}",
        }
    value = float(claim["value"])
    phys, phys_why = _physics_pass(spec, value)
    err = abs(value - float(spec["gt_value"]))
    band = band_score(err, pass_tol(spec))
    score = phys * band  # source already verified (=1)
    gross = gross_error_tol(spec)
    return {
        "value":           value,
        "gt_value":        float(spec["gt_value"]),
        # The continuous error is kept whatever the score is: it is what a
        # buyer shapes a reward on, and it is what makes an offline rescore
        # under a different band possible without re-running anything.
        "absolute_error":  err,
        "pass_tol":        pass_tol(spec),
        "kpi_score":       round(score, 4),
        "source_verified": 1.0,
        "physics_pass":    round(phys, 4),
        "physics_why":     phys_why,
        "band_pass":       round(band, 4),
        # Diagnostic only — never multiplied into the score. It is what
        # separates "computed the wrong number" from "not in the right
        # postcode" when a failure is attributed.
        "gross_error_tol": gross,
        "gross_error":     None if gross is None else bool(err > gross),
        "extracted":       pv.extracted,
    }


def numerics_gate(kpis_spec: dict, per_kpi: dict) -> tuple[float, dict]:
    """Binary gate on the run's own numerical quality. 1.0 = pass, 0.0 = fail.

    A KPI opts in by declaring a one-sided limit, e.g.

        "final_residual_U": { ..., "gate": {"max": 0.005} }
        "mesh_cell_count":  { ..., "gate": {"min": 4096} }

    One-sided and declared per case on purpose. The right test differs by
    quantity and cannot be inferred from the name: exceeding a residual ceiling
    means the run has not converged, while exceeding a cell count means the mesh
    is *finer* than the reference, which is not a defect. Nothing here reads
    `physics_min`/`gross_error_tol` implicitly — the case states the limit.

    **Absence never fails the gate.** A process signal the agent did not report,
    or one whose provenance did not verify, is recorded as `absent` and skipped.
    Zeroing a case for a signal the task did not extract is the L1 failure mode
    this benchmark spent three real defects learning to avoid (docs/acceptance.md);
    an omitted signal is visible in this detail block instead.
    """
    checks: list[dict] = []
    failures: list[str] = []
    for name, spec in kpis_spec.items():
        gate = spec.get("gate")
        if not isinstance(gate, dict) or not gate:
            continue
        res = per_kpi.get(name) or {}
        value = res.get("value")
        if res.get("source_verified") != 1.0 or not isinstance(value, (int, float)):
            checks.append({"kpi": name, "status": "absent", "gate": gate})
            continue
        value = float(value)
        low, high = gate.get("min"), gate.get("max")
        if low is not None and value < float(low):
            failures.append(f"{name}={value:g} below the required minimum {float(low):g}")
            checks.append({"kpi": name, "status": "fail", "value": value, "gate": gate})
        elif high is not None and value > float(high):
            failures.append(f"{name}={value:g} above the allowed maximum {float(high):g}")
            checks.append({"kpi": name, "status": "fail", "value": value, "gate": gate})
        else:
            checks.append({"kpi": name, "status": "pass", "value": value, "gate": gate})
    ok = 0.0 if failures else 1.0
    detail = {"numerics_ok": ok, "checks": checks}
    if failures:
        detail["why"] = "; ".join(failures)
    elif not checks:
        detail["why"] = "no KPI declares a numerics gate"
    return ok, detail


def _score_groups(
    kpis_spec: dict,
    groups_spec: dict,
    result: dict,
    sim_records: list[dict],
    case_dir=None,
    solver_label=None,
) -> tuple[float, dict]:
    """Run each KPI through verification + scoring; aggregate by group.

    Group score = unweighted mean of its member KPI scores. The missing-from-
    `kpis.json` case is rejected at the spec layer; a member declared in
    spec but absent from result.json scores 0 (penalising "skipped a stage").
    """
    from .failure_class import annotate_per_kpi

    per_kpi: dict[str, dict] = {}
    for name, spec in kpis_spec.items():
        per_kpi[name] = _score_one_kpi(name, spec, result.get(name), sim_records)

    # Annotate each per-KPI dict with two-axis failure classification
    # (provenance_stage × solver_stage) plus a v3.1 backward-compat
    # `failure_class` field. The trial-context arguments — sim_records,
    # case_dir, solver_label — are bundled into a TrialContext that the
    # detector plugin layer (Phase 3a) routes to every applicable
    # detector. See sim-proj #125 RFC.
    axis_counts = annotate_per_kpi(
        per_kpi, result, sim_records,
        case_dir=case_dir, solver_label=solver_label,
    )

    per_group: dict[str, dict] = {}
    weighted_sum = 0.0
    for gname, gspec in groups_spec.items():
        weight = float(gspec["weight"])
        members = [n for n, s in kpis_spec.items() if s.get("group") == gname]
        if not members:
            per_group[gname] = {"weight": weight, "members": [], "group_score": 0.0, "why": "no KPIs declared for this group"}
            continue
        m_scores = [per_kpi[n]["kpi_score"] for n in members]
        gscore = sum(m_scores) / len(m_scores)
        per_group[gname] = {
            "weight":      weight,
            "members":     members,
            "group_score": round(gscore, 4),
        }
        weighted_sum += weight * gscore
    return round(weighted_sum, 4), {
        "per_group": per_group,
        "per_kpi": per_kpi,
        # New canonical axis distributions (reward-v3.2)
        "solver_stage_counts":     axis_counts["solver_stage_counts"],
        "provenance_stage_counts": axis_counts["provenance_stage_counts"],
    }


# ──────────────────────────────────────────────────────────────────────
# Aggregation + CLI
# ──────────────────────────────────────────────────────────────────────

# Meta layer is diagnostic-only since 2026-04-29 — see module docstring.
# The previous (0.10, 0.90) split is preserved in git for reference.
W_META = 0.0
W_KPI  = 1.0


def _validate_groups(groups_spec: dict, kpis_spec: dict) -> str | None:
    if not groups_spec:
        return "kpis.json has no `kpi_groups`"
    total = sum(float(g.get("weight", 0)) for g in groups_spec.values())
    if abs(total - 1.0) > 1e-6:
        return f"kpi_groups weights sum to {total} (must be 1.0)"
    if not kpis_spec:
        return "kpis.json has no `kpis`"
    for name, spec in kpis_spec.items():
        g = spec.get("group")
        if g not in groups_spec:
            return f"kpi {name!r} declares group {g!r} which is not in kpi_groups"
    for gname, gspec in groups_spec.items():
        weight = float(gspec.get("weight", 0))
        members = [name for name, spec in kpis_spec.items() if spec.get("group") == gname]
        if weight > 0 and not members:
            return f"kpi_group {gname!r} has positive weight but no KPIs"
    return None


def main(argv: list[str] | None = None) -> int:
    from .contract import KPIS_PATH, AGENT_RESULT, REWARD_OUT
    ap = argparse.ArgumentParser(description="sim-benchmark solver-neutral grader (v3)")
    ap.add_argument("--kpis", type=Path, default=Path(KPIS_PATH),
                    help=f"Path to kpis.json (default: {KPIS_PATH})")
    ap.add_argument("--result", type=Path, default=Path(AGENT_RESULT),
                    help=f"Agent's result.json (default: {AGENT_RESULT})")
    ap.add_argument("--reward-out", type=Path, default=Path(REWARD_OUT),
                    help=f"Output reward.json path (default: {REWARD_OUT})")
    args = ap.parse_args(argv)

    spec = json.loads(args.kpis.read_text(encoding="utf-8"))
    kpis_spec = spec.get("kpis", {})
    groups_spec = spec.get("kpi_groups", {})

    # Trial context for the solver-stage detector layer.
    # case_dir = where the agent put result.json — typically /tmp/agent
    # in container, or tmp_path in tests. Detectors scan this for
    # solver artifacts (log files, output dirs, etc.).
    case_dir = args.result.parent if args.result else None
    # solver_label = case-author's declared solver, when task.toml is
    # alongside the kpis.json (cases/<x>/tests/kpis.json sits next to
    # cases/<x>/task.toml).
    solver_label: str | None = None
    task_type: str | None = None
    n_evals_min: int = 0
    design_table: str | None = None
    task_toml = args.kpis.parent.parent / "task.toml"
    if task_toml.is_file():
        try:
            import tomllib
        except ModuleNotFoundError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ModuleNotFoundError:
                tomllib = None  # type: ignore[assignment]
        if tomllib is not None:
            try:
                tdata = tomllib.loads(task_toml.read_text(encoding="utf-8"))
                sim_meta = ((tdata.get("metadata") or {}).get("sim") or {})
                solver_label = sim_meta.get("solver")
                # Study task types (optimization / sensitivity) run the solver
                # many times; n_evals_min sets the study-evidence threshold.
                task_type = sim_meta.get("task_type")
                study_cfg = sim_meta.get("study") or {}
                n_evals_min = int(study_cfg.get("n_evals_min", 0) or 0)
                design_table = study_cfg.get("design_table")
            except Exception:
                pass

    # Fall back to the evaluator's own spec when task.toml is not reachable.
    #
    # It often is not. Under a separate verifier container the evaluator package
    # is mounted on its own — `tests/` becomes `/tests`, so the lookup above
    # resolves to `/task.toml` and finds nothing. The failure is silent and
    # one-directional: `solver_label` stays None, and a None label means the
    # artifact gate is *skipped*, so a case that should hard-zero a run with no
    # solver evidence quietly stops checking. Nothing in the score says so.
    #
    # `tests/kpis.json` is the evaluator's own package and travels with it, so it
    # is the right place for a fact the evaluator needs. task.toml stays
    # authoritative when present — it is the catalog field — and lint_case.py
    # holds the two in agreement.
    if solver_label is None:
        try:
            solver_label = (json.loads(args.kpis.read_text(encoding="utf-8"))
                            .get("solver"))
        except Exception:  # noqa: BLE001
            pass

    spec_err = _validate_groups(groups_spec, kpis_spec)
    if spec_err:
        # Hard fail — the case is malformed; refuse to score.
        out = {"score": 0.0}
        args.reward_out.parent.mkdir(parents=True, exist_ok=True)
        args.reward_out.write_text(json.dumps(out, indent=2))
        args.reward_out.with_name("reward_detail.json").write_text(
            json.dumps({"schema_version": "reward-v3", "error": spec_err}, indent=2)
        )
        return 1

    # Meta layer.
    # We *read* records from meta_detail for KPI verification + solver-stage
    # attribution, but leave them in the dict so saved reward_detail.json
    # carries them — necessary for back-fills and the failure_class
    # detector to attribute L2_solver_crash post-hoc.
    meta_score, meta_detail = meta_check()
    sim_records = meta_detail.get("records", [])

    # Result.json (may be missing — every KPI then scores 0 with "absent")
    result_obj: dict = {}
    result_err: str | None = None
    if args.result.is_file():
        try:
            result_obj = json.loads(args.result.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            result_err = f"result.json invalid JSON: {e}"
    else:
        result_err = f"result.json not found at {args.result}"

    # KPI layer
    if result_err:
        kpi_score = 0.0
        kpi_detail = {"why": result_err}
    else:
        kpi_score, kpi_detail = _score_groups(
            kpis_spec, groups_spec, result_obj, sim_records,
            case_dir=case_dir, solver_label=solver_label,
        )

    # ── Anti-cheat: hard-zero analytical shortcuts ─────────────────────
    # The agent who hand-calculates a value and writes it to a text file
    # will pass per-KPI provenance (source file exists, extract pipeline
    # runs) but fails the solver-evidence check below: no .mph /
    # log.<of_solver> / LTspice .log on disk = no real solve = hard zero.
    #
    # **No live case reaches this code** (#196): all 127 under `cases/[!_]*/`
    # call `native_cantera`, `native_pybamm`, `openfoam_interface` or
    # `calculix_interface` directly. This path serves `cases/_phase2/`, so a
    # change here moves no phase-1 number -- and equally, hardening it buys
    # phase 1 nothing.
    #
    # Cross-solver: works for any solver with a registered detector that
    # exposes ``has_solver_evidence``. Unknown solver / no solver_label =
    # gate skipped (preserves legacy behaviour). See sim-proj #125 RFC.
    from .detectors import TrialContext, has_solver_evidence, has_study_evidence
    ctx = TrialContext(
        sim_records=sim_records, case_dir=case_dir, solver_label=solver_label,
    )
    solver_evidence = has_solver_evidence(ctx)
    anti_cheat_detail: dict = {
        "solver_label":    solver_label,
        "solver_evidence": solver_evidence,
    }
    # Study task types additionally require evidence of a multi-run study
    # (>= n_evals_min solver evaluations over a parameter space): a design
    # table or that many legacy run records. Stops "run once + guess the optimum".
    is_study = task_type in ("optimization", "sensitivity") and n_evals_min > 1
    study_evidence = (
        has_study_evidence(ctx, n_evals_min, design_table) if is_study else True
    )
    if is_study:
        anti_cheat_detail["task_type"] = task_type
        anti_cheat_detail["n_evals_min"] = n_evals_min
        anti_cheat_detail["study_evidence"] = study_evidence

    gate_failed = (solver_label is not None and not solver_evidence) or \
                  (is_study and not study_evidence)
    if gate_failed:
        if solver_label is not None and not solver_evidence:
            reason = (f"no {solver_label!r} solver evidence in {case_dir} - "
                      "analytical-shortcut rejected; final_score set to 0")
        else:
            reason = (f"no study evidence (need a design table with >= "
                      f"{n_evals_min} runs or that many sim runs) in {case_dir}"
                      f" - {task_type} shortcut rejected; final_score set to 0")
        anti_cheat_detail["why"] = reason
        # Preserve the original per-KPI scoring detail for forensics, but
        # hard-zero the aggregate.
        kpi_detail["pre_anti_cheat_kpi_score"] = kpi_score
        kpi_score = 0.0

    # Numerics gate — convergence / mesh adequacy, declared per KPI. Applied
    # after the anti-cheat zeroing so a gated run keeps its forensic breakdown.
    numerics_ok, numerics_detail = numerics_gate(
        spec.get("kpis", {}), kpi_detail.get("per_kpi", {})
    )
    if numerics_ok < 1.0:
        kpi_detail["pre_numerics_gate_kpi_score"] = kpi_score
        kpi_score = round(kpi_score * numerics_ok, 4)

    final = round(W_META * meta_score + W_KPI * kpi_score, 4)

    # Harbor contract: reward.json has exactly one key.
    args.reward_out.parent.mkdir(parents=True, exist_ok=True)
    args.reward_out.write_text(json.dumps({"score": final}, indent=2))

    detail = {
        "schema_version": "reward-v3.3",
        "case_id":        spec.get("case_id", args.kpis.parent.name),
        "weights":        {"meta": W_META, "kpi": W_KPI},
        "meta_score":     round(meta_score, 4),
        "meta_detail":    meta_detail,
        "anti_cheat":     anti_cheat_detail,
        "numerics_gate":  numerics_detail,
        "kpi_score":      kpi_score,
        "kpi_detail":     kpi_detail,
        "final_score":    final,
    }
    args.reward_out.with_name("reward_detail.json").write_text(
        json.dumps(detail, indent=2)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
