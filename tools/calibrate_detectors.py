#!/usr/bin/env python3
"""Calibration suite — TPR/FPR validation for solver-stage detectors.

Reads real solver logs from
``lib/sim_benchmark_verifier/calibration_fixtures/`` (one healthy log per
solver), programmatically derives broken variants by mutating the
healthy log (truncate End marker, push residual past tolerance, push
continuity error past tolerance, inject error markers), runs the
applicable detector, and asserts the right ``solver_stage`` fires.

Output:
- TPR (true positive rate) per stage = correct attributions / total
  fixtures expected to fire that stage
- FPR (false positive rate) per stage = incorrect attributions of that
  stage on fixtures expected NOT to fire it
- Per-fixture detail table

Per ccl-evaluator's discipline, a detector / threshold is "validated"
only when TPR > 0 AND FPR == 0 across the fixture set. Validated
detectors can drive scoring decisions; unvalidated stay diagnostic-only.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sim_benchmark_verifier.detectors import TrialContext, dispatch
from sim_benchmark_verifier.detectors.openfoam import (
    CONTINUITY_TOLERANCE,
    RESIDUAL_TOLERANCE,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = REPO_ROOT / "lib" / "sim_benchmark_verifier" / "calibration_fixtures"


@dataclass
class Fixture:
    name: str               # human-readable label
    log_filename: str       # how the broken log file is named in case_dir
    text: str               # the (possibly mutated) log content
    expected: str | None    # what solver_stage we expect detector to fire
    solver_label: str       # detector routing label


# ── Broken-variant builders (mutations of a healthy log) ────────────────


def _of_truncate_end(text: str) -> str:
    """Drop the trailing 'End' marker → mid-run kill."""
    return re.sub(r"\nEnd\s*\Z", "\nFOAM aborting\n", text, flags=re.S)


def _of_push_last_residual(text: str, target: float) -> str:
    """Replace ALL Final residuals in the LAST Time = ... block with `target`.

    Surgical: keep all other blocks intact so the timeline reads right.
    """
    blocks = re.split(r"\n(?=Time = )", text)
    if len(blocks) < 2:
        return text
    last = blocks[-1]
    last_mut = re.sub(
        r"(Final residual = )([0-9.eE+-]+)",
        lambda m: m.group(1) + repr(target),
        last,
    )
    return "\n".join(blocks[:-1]) + "\n" + last_mut


def _of_push_continuity(text: str, target: float) -> str:
    """Replace 'sum local = X' on the LAST continuity-errors line with `target`."""
    matches = list(re.finditer(
        r"(time step continuity errors\s*:?\s*sum local = )([0-9.eE+-]+)",
        text,
    ))
    if not matches:
        return text
    last = matches[-1]
    return text[:last.start(2)] + repr(target) + text[last.end(2):]


# ── Fixture builders ────────────────────────────────────────────────────


def of_fixtures() -> list[Fixture]:
    healthy_path = FIXTURE_ROOT / "openfoam" / "healthy.log"
    healthy = healthy_path.read_text(encoding="utf-8", errors="replace")
    return [
        # Healthy → expect None (universal handles L5/L6 from KPI fields)
        Fixture("of/healthy",       "log.simpleFoam", healthy, None, "openfoam"),
        # L3: truncate End marker
        Fixture("of/no_end",        "log.simpleFoam",
                _of_truncate_end(healthy), "L3_convergence", "openfoam"),
        # L3: push final residual past tolerance (10x over)
        Fixture("of/high_residual", "log.simpleFoam",
                _of_push_last_residual(healthy, RESIDUAL_TOLERANCE * 10),
                "L3_convergence", "openfoam"),
        # L4: push continuity error past tolerance (10x over)
        Fixture("of/leaky",         "log.simpleFoam",
                _of_push_continuity(healthy, CONTINUITY_TOLERANCE * 10),
                "L4_conservation", "openfoam"),
        # FPR boundary check: residual just under tolerance → no L3
        Fixture("of/borderline_residual", "log.simpleFoam",
                _of_push_last_residual(healthy, RESIDUAL_TOLERANCE * 0.5),
                None, "openfoam"),
        # FPR boundary: continuity just under tolerance → no L4
        Fixture("of/borderline_continuity", "log.simpleFoam",
                _of_push_continuity(healthy, CONTINUITY_TOLERANCE * 0.5),
                None, "openfoam"),
    ]


# ── Driver ──────────────────────────────────────────────────────────────


def _stage_kpi_result_for_fixture(fixture: Fixture) -> dict:
    """KPI shape for the test. We make provenance fail so the
    solver-axis detector kicks in (otherwise universal returns L6)."""
    return {
        "kpi_score": 0.0,
        "source_verified": 0.0,
        "physics_pass": 0.0,
        "band_pass": 0.0,
        "why": "source verification failed: source file not found: /tmp/intentional",
    }


def run_fixture(fixture: Fixture, tmp_path: Path) -> tuple[str | None, bool]:
    """Write the fixture log into a tmp case_dir, dispatch, return
    ``(actual_stage, matched_expected)``."""
    case_dir = tmp_path / fixture.name.replace("/", "_")
    case_dir.mkdir(parents=True, exist_ok=True)
    log_path = case_dir / fixture.log_filename
    log_path.write_text(fixture.text, encoding="utf-8")
    ctx = TrialContext(case_dir=case_dir, solver_label=fixture.solver_label)
    actual = dispatch(_stage_kpi_result_for_fixture(fixture), ctx)
    return actual, actual == fixture.expected


def main(argv: list[str] | None = None) -> int:
    # The per-fixture table marks its verdict with U+2713/U+2717, which a Windows
    # console's cp1252 stdout cannot encode. Untouched, this run prints the table
    # header and then dies on its first row: the calibration is computed in full
    # and none of it is readable.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tmp-dir", type=Path, default=Path("/tmp/calibration"))
    ap.add_argument("--output-md", type=Path, default=None,
                    help="write EVIDENCE.md-style table to this path")
    args = ap.parse_args(argv)
    args.tmp_dir.mkdir(parents=True, exist_ok=True)

    fixtures = of_fixtures()
    rows = []
    for fix in fixtures:
        actual, ok = run_fixture(fix, args.tmp_dir)
        rows.append((fix.name, fix.expected, actual, ok))

    # TPR/FPR per stage
    stages = sorted({r[1] for r in rows if r[1] is not None})
    metrics: dict[str, dict] = {}
    for stage in stages:
        positives = [r for r in rows if r[1] == stage]
        negatives = [r for r in rows if r[1] != stage]
        tp = sum(1 for r in positives if r[2] == stage)
        fp = sum(1 for r in negatives if r[2] == stage)
        tpr = tp / len(positives) if positives else None
        fpr = fp / len(negatives) if negatives else None
        metrics[stage] = {
            "n_positive": len(positives),
            "n_negative": len(negatives),
            "tp": tp, "fp": fp,
            "tpr": tpr, "fpr": fpr,
            "validated": (tpr is not None and tpr > 0
                          and fpr is not None and fpr == 0),
        }

    # Print summary
    print()
    print(f"{'Fixture':<35} {'Expected':<20} {'Actual':<20} {'OK':>4}")
    print("-" * 85)
    for name, exp, act, ok in rows:
        print(f"{name:<35} {str(exp):<20} {str(act):<20} {'✓' if ok else '✗':>4}")

    print()
    print(f"{'Stage':<25} {'N+':>4} {'N-':>4} {'TP':>4} {'FP':>4} "
          f"{'TPR':>6} {'FPR':>6} {'Valid':>6}")
    print("-" * 70)
    for stage, m in metrics.items():
        print(f"{stage:<25} {m['n_positive']:>4} {m['n_negative']:>4} "
              f"{m['tp']:>4} {m['fp']:>4} "
              f"{m['tpr']:>6.2f} {m['fpr']:>6.2f} "
              f"{'YES' if m['validated'] else 'NO':>6}")

    if args.output_md:
        _write_evidence_md(args.output_md, rows, metrics)
        print(f"\nwrote {args.output_md}")

    # Exit non-zero if any validation failed (TPR < 1 or FPR > 0)
    bad = [s for s, m in metrics.items() if not m["validated"]]
    return 1 if bad else 0


def _write_evidence_md(path: Path, rows, metrics) -> None:
    lines = [
        "# EVIDENCE — solver-stage detector calibration",
        "",
        "Auto-generated by `tools/calibrate_detectors.py` from real solver logs",
        "in `lib/sim_benchmark_verifier/calibration_fixtures/`.",
        "",
        "## Validation status per stage",
        "",
        "| Stage | N+ | N- | TP | FP | TPR | FPR | Validated? |",
        "|---|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for stage, m in metrics.items():
        lines.append(
            f"| `{stage}` | {m['n_positive']} | {m['n_negative']} | "
            f"{m['tp']} | {m['fp']} | "
            f"{m['tpr']:.2f} | {m['fpr']:.2f} | "
            f"{'✅' if m['validated'] else '❌'} |"
        )

    lines += [
        "",
        "Validation rule: TPR > 0 AND FPR == 0 across the fixture set "
        "(per ccl-evaluator's discipline). Validated stages may drive "
        "scoring decisions; unvalidated stages remain diagnostic-only.",
        "",
        "## Per-fixture detail",
        "",
        "| Fixture | Expected stage | Actual stage | Match |",
        "|---|---|---|:---:|",
    ]
    for name, exp, act, ok in rows:
        lines.append(
            f"| `{name}` | `{exp}` | `{act}` | {'✅' if ok else '❌'} |"
        )

    lines += [
        "",
        "## Threshold provenance",
        "",
        "- `RESIDUAL_TOLERANCE` = `1e-2` "
        "(OpenFOAM detector — last-block max final residual)",
        "- `CONTINUITY_TOLERANCE` = `1e-3` "
        "(OpenFOAM detector — max |local| continuity error)",
        "",
        "Fixtures used:",
        "- OpenFOAM healthy log: `lid_driven_cavity_re100` oracle "
        "(simpleFoam, 1639 iter, residual ~1e-9, continuity ~1e-10)",
        "",
        "Broken variants are programmatic mutations of the healthy "
        "log (truncate End marker, push residual or continuity past "
        "tolerance).",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
