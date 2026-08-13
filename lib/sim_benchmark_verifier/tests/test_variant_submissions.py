"""Verification (e): a submission that is NUMERICALLY RIGHT but written differently.

The case-PR bar checks two points on a line -- the oracle (must score exactly
1.0 since #359) and a deliberately broken run (must score < 0.5). Neither exercises the band in
between: a correct answer produced by a legitimately different route. Every one
of #68's three defects lives exactly there, and all three passed their oracle,
because the oracle never takes that route (#93).

Copying a template 39 times copies its blind spot 39 times, and (c)+(d) cannot
see it however often they run.

The combustion tests were `xfail(strict=True)` while #68's defects stood, and
battery's copy of the first of them (#138) sat there after that, so that a known
hole could not rot into a permanently-yellow test nobody reads. All are fixed and
no marker is left; what remains is the regression, and each test drives the real
code path rather than restating what the code says. A variant test that finds a
defect it is not allowed to fix goes back on `xfail(strict=True)` with the issue
number in its reason.

**One variant per shared evaluator covers every case that dispatches to it**,
and that is what makes (e) affordable: a case's `tests/verify*.py` is eight
lines around `from sim_benchmark_verifier.<module> import main_from_case`, so
the module is where the whole scoring chain lives and the module is where the
variant belongs. Four modules carry the live tracks today --
`native_cantera` (combustion), `native_pybamm` (battery),
`calculix_interface` (packaging), `openfoam_interface` (cfd) -- and
`test_every_shared_evaluator_has_a_variant_submission` discovers that list from
the cases themselves rather than restating it here, so the next generated
family is gated the day its first case lands.

That last test is the difference between this file and a one-off. #93's whole
finding is that a blind spot copied 39 times is invisible to (c)+(d); a list of
tracks maintained by hand is the same failure one level up.
"""
from __future__ import annotations

import ast
import re
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim_benchmark_verifier import calculix_interface as cx  # noqa: E402
from sim_benchmark_verifier import native_cantera as nc  # noqa: E402
from sim_benchmark_verifier import native_pybamm as nb  # noqa: E402
from sim_benchmark_verifier import openfoam_interface as of  # noqa: E402
from sim_benchmark_verifier.csv_interface import EvaluationFailure  # noqa: E402


# ── combustion — native_cantera ─────────────────────────────────────────


def _spec():
    return nc.CanteraSpec(
        case_id="variant", kind="idt", mechanism="gri30.yaml", fuel="CH4",
        phi=1.0, T0_K=1400.0, P0_atm=1.0,
    )


# The first forty lines of a real `Sim1D.save("flame_solution.yaml",
# name="soln")` on Cantera 3.2.0, trimmed. Exactly one key at column 0 -- the
# name the solution was stored under -- and every mechanism-looking word
# ("phase", "species" via the mass-fraction block) indented beneath it.
SIM1D_SAVE_YAML = """\
soln:
  description: probe
  generator: Cantera SolutionArray
  cantera-version: 3.2.0
  date: Tue Aug  4 08:47:43 2026
  reactants:
    type: inlet
    size: 1
    transport-model: mixture-averaged
    points: 1
    mass-flux: 0.4476745969646139
    temperature: 300.0
    pressure: 1.01325e+05
    mass-fractions:
      O2: 0.2201412376866278
      CH4: 0.05518666598235154
      N2: 0.7246720963310207
  flame:
    type: free-flow
    size: 53
    points: 53
    phase:
      name: gri30
      source: /usr/lib/python3/site-packages/cantera/data/gri30.yaml
    energy-enabled: true
    grid: [0.0, 0.0006, 0.0012]
    velocity: [0.3812, 0.3901, 0.4033]
    T: [300.0, 301.7, 305.2]
"""

# ...and the head of a Cantera mechanism, which declares its sets at column 0.
MECHANISM_YAML = """\
description: |-
  a reduced mechanism the submission shipped alongside its driver
generator: ck2yaml
cantera-version: 2.5.0
units: {length: cm, time: s, quantity: mol, activation-energy: cal/mol}

phases:
- name: gas
  thermo: ideal-gas
  elements: [O, H, C, N]
  species: [H2, O2, CH4, N2]
  kinetics: gas
  state: {T: 300.0, P: 1 atm}

species:
- name: H2
  composition: {H: 2}

reactions:
- equation: H2 + O2 <=> 2 OH
  rate-constant: {A: 1.7e+13, b: 0.0, Ea: 47780.0}
"""

# What Cantera actually does when the field is already there, so the toy driver
# fails for the same reason the real one did:
#   CanteraError thrown by SolutionArray::writeHeader:
#   Field name 'soln' exists; use 'overwrite' argument to overwrite.
DRIVER = """\
import os, csv, sys

if os.path.exists("flame_solution.yaml"):
    sys.exit("Field name 'soln' exists; use 'overwrite' argument to overwrite.")

assert os.path.exists("mech.yaml"), "the mechanism the driver loads is missing"

with open("results.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["time_s", "T_K", "P_Pa"])
    for i in range(40):
        w.writerow([i * 1e-5, 1400.0 + 35.0 * i, 101325.0])

open("flame_solution.yaml", "w").write("soln:\\n  generator: Cantera SolutionArray\\n")
open("profile.png", "wb").write(b"\\x89PNG")
"""


def _submission(tmp_path: Path) -> Path:
    sub = tmp_path / "submission"
    sub.mkdir()
    (sub / "run_case.py").write_text(DRIVER, encoding="utf-8")
    (sub / "mech.yaml").write_text(MECHANISM_YAML, encoding="utf-8")
    (sub / "flame_solution.yaml").write_text(SIM1D_SAVE_YAML, encoding="utf-8")
    (sub / "results.csv").write_text("time_s,T_K\n0.0,1400.0\n", encoding="utf-8")
    (sub / "profile.png").write_bytes(b"\x89PNG")
    return sub


def test_a_saved_solution_is_stripped_while_the_mechanism_it_shares_a_suffix_with_is_kept(
    tmp_path: Path,
):
    """A submission that stores its solution the way Cantera documents.

    `Sim1D.save()` writes YAML by default, and `.yaml` was on the keep-list
    because MECHANISM files are YAML too -- so the submission's own output was
    carried into the clean reproduction directory, and the driver re-running
    from there hit a field that already existed and died. Score 0.0 for using
    the documented idiom; an agent that avoided it passed.

    Driven through `_reproduce` rather than through the keep-list, because the
    keep-list is no longer where the whole decision lives and a test that
    restates the implementation cannot fail when the implementation is wrong.
    """
    sub = _submission(tmp_path)
    work = tmp_path / "work"

    out = nc._reproduce(sub, work, _spec(), tmp_path / "logs")

    assert out["exit_code"] == 0
    # The mechanism is an input and the re-run needs it.
    assert (work / "mech.yaml").is_file()
    # The saved solution is an artefact of the graded run; sharing a suffix
    # with the mechanism must not be enough to carry it in.
    assert "flame_solution.yaml" in out["stripped_files"]
    # And the numeric artefacts are still stripped, as they always were.
    assert "results.csv" in out["stripped_files"]
    assert "profile.png" in out["stripped_files"]
    # Whatever the reproduction directory holds now came out of this run.
    assert (work / "results.csv").is_file()


def test_a_repeated_timestamp_is_refused_not_scored():
    """A coarser output grid, which the interface permits, must not score wrong.

    `extract_ignition_delay` checked that its INPUTS were finite and never
    checked the derivative it computes itself. One repeated timestamp makes
    `np.gradient` divide by zero, `argmax` then picks the NaN, and the function
    returned a confident ignition delay next to `max_dTdt_K_per_s = nan`.

    Observed on a real trial: the evaluator's own reproduction printed 19.75 us
    while its extraction reported 0.060 ms, and the case scored 0.1. Refusing
    to extract is correct here; returning a number is not.
    """
    n = 60
    t = [i * 1e-6 for i in range(n)]
    t[30] = t[29]                                  # a legal, coarser sampling
    temp = [1400.0 + (900.0 if i > 30 else 0.0) for i in range(n)]

    with pytest.raises(RuntimeError, match="does not advance"):
        nc.extract_ignition_delay({"time_s": t, "T_K": temp}, _spec())


def test_the_same_trace_without_the_repeat_still_extracts():
    """The refusal above must be about the degenerate interval and nothing else.

    Without this, "refuse on a coarse grid" and "refuse on a repeated
    timestamp" are indistinguishable, and the first would be a new way to zero
    a correct submission.
    """
    n = 60
    t = [i * 1e-6 for i in range(n)]
    temp = [1400.0 + (900.0 if i > 30 else 0.0) for i in range(n)]

    out = nc.extract_ignition_delay({"time_s": t, "T_K": temp}, _spec())

    assert out["max_dTdt_K_per_s"] > 0.0
    assert out["ignition_delay_ms"] == pytest.approx(t[30] * 1e3, rel=0.2)


# ── combustion — "a coarser output grid", the (e) clause by name (#189) ──
#
# `extract_ignition_delay` reads the delay off the submitted samples with no
# interpolation and no peak refinement, so the answer can only move in steps of
# the output grid. 22 of the 31 ignition cases have a reference below 0.24 ms
# and the smallest is 1.84 us against a 92 ns band, so an ordinary reporting
# step -- a microsecond -- is an order of magnitude wider than the whole
# tolerance. Reproduced in the real combustion image before this was written: a
# submission with the oracle's mechanism, reactor and integrator tolerances,
# differing only in writing on a 1 us grid, scored 0.1 with every gate passing
# and `gross_error: false` beside it. Nothing in `reward_detail.json` said the
# grid was the reason, so in an aggregate it was indistinguishable from a model
# that got the physics wrong.
#
# What these pin is the attribution. The score is deliberately untouched.


def _logistic_trace_driver(dt_s: float, *, t_ignition_s: float = 1.1e-3,
                           t_end_s: float = 5.0e-3) -> str:
    """A driver writing a smooth ignition-shaped trace on a chosen output grid.

    Synthetic on purpose: what is under test is the output interface, not the
    chemistry, and CI has no Cantera. The dT/dt maximum sits exactly at
    `t_ignition_s`, so the delay a correct run reports is known in closed form
    and the only thing that moves it is the grid it is written on.
    """
    return textwrap.dedent(f"""\
        import csv, math
        DT, T_IGN, T_END, TAU = {dt_s!r}, {t_ignition_s!r}, {t_end_s!r}, 2.0e-5
        rows, i = [], 0
        while True:
            t = i * DT
            if t > T_END:
                break
            T = 1400.0 + 1200.0 / (1.0 + math.exp(-(t - T_IGN) / TAU))
            rows.append((t, T, 101325.0 * T / 1400.0))
            i += 1
        with open("results.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time_s", "T_K", "P_Pa"])
            w.writerows(rows)
        open("history.png", "wb").write(b"\\x89PNG\\r\\n\\x1a\\n")
        """)


def _idt_kpis(gt_ms: float, pass_tol_ms: float) -> dict:
    return {
        "kpi_groups": {"outputs": {"weight": 1.0}},
        "kpis": {
            "ignition_delay_ms": {
                "group": "outputs", "shape": "scalar", "unit": "ms",
                "gt_value": gt_ms, "physics_min": 0.0, "physics_max": 50.0,
                "pass_tol": pass_tol_ms, "gross_error_tol": pass_tol_ms * 4,
                "weight": 1.0,
            }
        },
    }


def _evaluate_trace(tmp_path: Path, monkeypatch, dt_s: float) -> dict:
    """Drive the real `evaluate` over a submission written on `dt_s` steps."""
    submission = tmp_path / f"sub-{dt_s:g}"
    submission.mkdir()
    (submission / "run_case.py").write_text(_logistic_trace_driver(dt_s),
                                            encoding="utf-8")
    # The equilibrium end state is the one dimension that needs Cantera and it
    # is orthogonal to what is under test; the trace saturates at 2600 K by
    # construction, so this is the state the library would return for it.
    monkeypatch.setattr(nc, "_equilibrium_state",
                        lambda spec: {"T_equilibrium_K": 2600.0,
                                      "P_equilibrium_Pa": 188175.0})
    return nc.evaluate(
        _spec(), nc.extract_ignition_delay,
        submission=submission,
        kpis=_idt_kpis(gt_ms=1.1, pass_tol_ms=0.05),
        reward_dir=tmp_path / f"reward-{dt_s:g}",
    )


def test_a_coarse_output_grid_is_attributed_by_name_not_left_as_a_bare_zero(
    tmp_path: Path, monkeypatch,
):
    """The zero has to say it came from the output grid, not from the physics.

    0.2 ms steps against a 0.05 ms band: the delay can only be reported at a
    sample, and the samples bracketing the true peak are four times the whole
    tolerance apart. The submission is otherwise correct.
    """
    detail = _evaluate_trace(tmp_path, monkeypatch, dt_s=2.0e-4)

    kpi = detail["dimensions"]["kpi_accuracy"]["per_kpi"]["ignition_delay_ms"]
    assert kpi["band_pass"] == 0.0
    assert kpi["resolves_pass_tol"] is False
    assert kpi["output_grid_spacing"] == pytest.approx(0.2)
    assert kpi["failure_kind"] == nc.OUTPUT_GRID_TOO_COARSE

    attribution = detail["attribution"]
    assert attribution["failure_kind"] == nc.OUTPUT_GRID_TOO_COARSE
    assert "ignition_delay_ms" in attribution["kpis"]

    # And the attribution is only an attribution: the KPI scores zero and
    # nothing here awards or removes credit. The zero is now a bare zero --
    # the figure used to put 0.1 under it (#195).
    assert detail["dimensions"]["kpi_accuracy"]["score"] == 0.0
    assert detail["dimensions"]["figure_produced"]["status"] == "pass"
    assert detail["gate_product"] == 1.0
    assert detail["final_score"] == pytest.approx(0.0)


def test_a_grid_that_does_resolve_the_band_is_scored_and_not_attributed(
    tmp_path: Path, monkeypatch,
):
    """The other direction, and the one that would make the check a new hazard.

    Same physics, same driver, 2 us steps. Nothing may appear in
    `reward_detail.json` about the output grid, and the score has to be the
    full one -- a resolution check that fires on a resolved trace would be a
    fresh way to zero a correct submission, which is the defect it exists to
    stop.
    """
    detail = _evaluate_trace(tmp_path, monkeypatch, dt_s=2.0e-6)

    kpi = detail["dimensions"]["kpi_accuracy"]["per_kpi"]["ignition_delay_ms"]
    assert kpi["value"] == pytest.approx(1.1, abs=1e-6)
    assert kpi["band_pass"] == 1.0
    assert kpi["resolves_pass_tol"] is True
    assert "failure_kind" not in kpi
    assert "attribution" not in detail
    assert detail["final_score"] == pytest.approx(1.0)


# ── combustion — "stopped soon after ignition", the (e) clause again (#125) ──
#
# The contract asks for an ignition delay and for a trace that covers the
# relaxation after the event. It does not ask the submission to integrate to
# thermodynamic equilibrium, and it cannot: a closed constant-volume reactor
# overshoots its UV equilibrium for a while after ignition and comes back down
# over tens to hundreds of times the delay. `equilibrium_consistent` compared
# the last row against equilibrium and MULTIPLIED into the score, so a
# submission that answered the question correctly and stopped where the prompt
# let it stop was scored 0.0 with `kpi_accuracy` sitting at 1.0 beside it.
# Four archived trials look exactly like that.
#
# The check is a recorded diagnostic now. What these two pin is that the
# separation actually holds in both directions: the diagnostic still fires, and
# firing costs nothing.


def _overshoot_driver(dt_s: float, end_offset_K: float, *,
                      t_ignition_s: float = 1.1e-3,
                      t_end_s: float = 1.4e-3) -> str:
    """An ignition trace truncated while still above equilibrium.

    `end_offset_K` is how far the LAST row sits above the 2600 K equilibrium
    the surrounding tests monkeypatch in. The overshoot decays on a time
    constant far longer than the ignition itself, which is the real shape --
    the radical pool burns down slowly -- so the trace is legitimate physics
    truncated at a legitimate place, not a wrong answer.
    """
    return textwrap.dedent(f"""\
        import csv, math
        DT, T_IGN, T_END, TAU = {dt_s!r}, {t_ignition_s!r}, {t_end_s!r}, 2.0e-5
        TREL = 4.0e-4
        OVER = {end_offset_K!r} / math.exp(-(T_END - T_IGN) / TREL)
        rows, i = [], 0
        while True:
            t = i * DT
            if t > T_END:
                break
            s = 1.0 / (1.0 + math.exp(-(t - T_IGN) / TAU))
            over = OVER * s * math.exp(-max(0.0, t - T_IGN) / TREL)
            T = 1400.0 + 1200.0 * s + over
            rows.append((t, T, 101325.0 * T / 1400.0))
            i += 1
        with open("results.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time_s", "T_K", "P_Pa"])
            w.writerows(rows)
        open("history.png", "wb").write(b"\\x89PNG\\r\\n\\x1a\\n")
        """)


def _evaluate_driver(tmp_path: Path, monkeypatch, source: str, name: str) -> dict:
    submission = tmp_path / name
    submission.mkdir()
    (submission / "run_case.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(nc, "_equilibrium_state",
                        lambda spec: {"T_equilibrium_K": 2600.0,
                                      "P_equilibrium_Pa": 188175.0})
    return nc.evaluate(
        _spec(), nc.extract_ignition_delay,
        submission=submission,
        kpis=_idt_kpis(gt_ms=1.1, pass_tol_ms=0.05),
        reward_dir=tmp_path / f"reward-{name}",
    )


def test_a_correct_run_stopped_before_the_end_state_settles_still_scores_full(
    tmp_path: Path, monkeypatch,
):
    """#125's four zeroed submissions, as a test.

    The last row sits 120 K above equilibrium -- 10% of the 1200 K rise, twice
    the diagnostic's band -- while the ignition delay is exact. The dimension
    must record `fail`, and the score must be 1.0 anyway.
    """
    detail = _evaluate_driver(tmp_path, monkeypatch,
                              _overshoot_driver(2.0e-6, 120.0), "early-stop")

    kpi = detail["dimensions"]["kpi_accuracy"]["per_kpi"]["ignition_delay_ms"]
    # The slow overshoot rides on top of the ignition step, so the dT/dt
    # maximum lands within a grid step of it -- well inside the 0.05 ms band.
    assert kpi["value"] == pytest.approx(1.1, abs=1e-2)
    assert kpi["band_pass"] == 1.0
    # The diagnostic fired ...
    assert detail["dimensions"]["equilibrium_consistent"]["status"] == "fail"
    # ... and it is not in the gate product, nor in the score.
    assert "equilibrium_consistent" not in detail["gates"]
    assert detail["gate_product"] == 1.0
    assert detail["final_score"] == pytest.approx(1.0)


def test_an_end_state_inside_the_band_records_a_pass(tmp_path: Path, monkeypatch):
    """The other direction: a diagnostic that always fails diagnoses nothing.

    Same driver, stopped where the measurement says a real trace stops -- 36 K,
    3% of the rise, the worst of the 31 operating points at the resolution the
    prompt asks for.
    """
    detail = _evaluate_driver(tmp_path, monkeypatch,
                              _overshoot_driver(2.0e-6, 36.0), "settled")

    eq = detail["dimensions"]["equilibrium_consistent"]
    assert eq["status"] == "pass"
    assert eq["rel_error_of_rise"] == pytest.approx(0.03, abs=5e-3)
    assert detail["final_score"] == pytest.approx(1.0)


def test_a_figure_is_recorded_and_pays_for_nothing(tmp_path: Path, monkeypatch):
    """#195, in the two directions that used to differ by 0.1.

    A right answer without a plot scored 0.9 and a wrong answer with one scored
    0.1. Neither weight was chosen for a reason, and after #188 made the band
    binary the second was a visible floor under every failure. The figure is
    still checked and still recorded; it is out of the scalar.
    """
    no_figure = _overshoot_driver(2.0e-6, 0.0).replace(
        'open("history.png", "wb").write(b"\\x89PNG\\r\\n\\x1a\\n")\n', "")
    detail = _evaluate_driver(tmp_path, monkeypatch, no_figure, "no-figure")
    assert detail["dimensions"]["figure_produced"]["status"] == "fail"
    assert detail["dimensions"]["kpi_accuracy"]["per_kpi"][
        "ignition_delay_ms"]["band_pass"] == 1.0
    assert detail["final_score"] == pytest.approx(1.0)

    # ... and a wrong answer that did draw one gets nothing for it.
    wrong = _overshoot_driver(2.0e-6, 0.0, t_ignition_s=2.2e-3, t_end_s=2.6e-3)
    detail = _evaluate_driver(tmp_path, monkeypatch, wrong, "wrong-with-figure")
    assert detail["dimensions"]["figure_produced"]["status"] == "pass"
    assert detail["dimensions"]["kpi_accuracy"]["per_kpi"][
        "ignition_delay_ms"]["band_pass"] == 0.0
    assert detail["final_score"] == pytest.approx(0.0)


def test_a_time_column_rounded_to_fixed_decimals_says_which_lattice_it_is_on():
    """`f"{t:.6f}"` is the common way to lose the delay, and it must be named.

    The rounding collapses samples onto one timestamp, so this arrives through
    the "time column does not advance" refusal rather than through the band.
    Same defect, and the message has to say so -- otherwise the author reads
    "your trace is broken" and looks at the integrator.
    """
    n = 60
    t = [float(f"{i * 2.5e-7:.6f}") for i in range(n)]
    temp = [1400.0 + (900.0 if i > 30 else 0.0) for i in range(n)]

    with pytest.raises(RuntimeError) as excinfo:
        nc.extract_ignition_delay({"time_s": t, "T_K": temp}, _spec())

    assert "multiple of 1e-06 s" in str(excinfo.value)
    assert excinfo.value.detail["failure_kind"] == nc.OUTPUT_GRID_TOO_COARSE


def test_a_yaml_that_is_neither_mechanism_nor_solution_is_treated_as_an_artefact(
    tmp_path: Path,
):
    """The classifier reads Cantera's declarations, not "does it look YAML-ish".

    Recorded because it is the cost of the fix: a hand-written config the
    driver reads back no longer survives into the reproduction. That is the
    deal `.json` and `.npz` already had -- a reproduction starts from source --
    and it is stated here so nobody rediscovers it as a surprise.
    """
    conf = tmp_path / "settings.yaml"
    conf.write_text(textwrap.dedent("""\
        grid_points: 400
        refine:
          slope: 0.05
        """), encoding="utf-8")

    assert not nc.is_reproduction_input(conf)


# ── battery — native_pybamm ─────────────────────────────────────────────


def _battery_spec():
    return nb.PyBaMMSpec(
        case_id="variant", kind="discharge", parameter_set="Chen2020", initial_soc=1.0,
    )


# A driver that solves and writes the declared interface. Pure Python, because
# what is under test is the evaluator's handling of the submission rather than
# PyBaMM's physics.
BATTERY_DRIVER = """\
import csv

with open("results.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["time_s", "current_A", "voltage_V"])
    for i in range(60):
        w.writerow([i * 60.0, 4.35, 4.15 - 0.0018 * i])
open("discharge.png", "wb").write(b"\\x89PNG")
"""

# The same driver split into a package the entry point imports -- a legal way to
# write the submission, and the shape that breaks if the strip takes source with
# it.
BATTERY_TRACE_MODULE = """\
import csv


def write():
    with open("results.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time_s", "current_A", "voltage_V"])
        for i in range(60):
            w.writerow([i * 60.0, 4.35, 4.15 - 0.0018 * i])
    open("discharge.png", "wb").write(b"\\x89PNG")
"""

# The head of a real `Solution.save_data("solution_data.json", ["Time [s]",
# "Current [A]", "Voltage [V]"], to_format="json")` on PyBaMM 26.7.1.0 -- the
# version `environment/domains/battery/tools.sh` pins -- after an SPMe/Chen2020
# 1 h discharge, truncated from 99 samples to 3. `save_data` json-dumps
# `get_data_dict()` through a numpy encoder, so the file is one line: a flat
# object keyed by PyBaMM's own variable names, each value the whole solved
# column. No nesting, no metadata, nothing that marks it as an artefact from
# the outside -- which is the point.
PYBAMM_SAVED_SOLUTION_JSON = (
    '{"Time [s]": [0.0, 0.00229176766624149, 0.00458353533248298], '
    '"Current [A]": [5.0, 5.0, 5.0], '
    '"Voltage [V]": [4.036292799684439, 4.036280533639291, 4.036268271466097]}'
)


def _battery_submission(tmp_path: Path) -> Path:
    sub = tmp_path / "submission"
    sub.mkdir()
    (sub / "run_case.py").write_text(BATTERY_DRIVER, encoding="utf-8")
    (sub / "solution_data.json").write_text(PYBAMM_SAVED_SOLUTION_JSON, encoding="utf-8")
    (sub / "sim.pkl").write_bytes(b"\x80\x04pickled-solution")
    (sub / "results.csv").write_text(
        "time_s,current_A,voltage_V\n0,4.35,4.15\n", encoding="utf-8")
    (sub / "discharge.png").write_bytes(b"\x89PNG")
    return sub


def test_a_saved_pybamm_solution_is_stripped_like_any_other_artefact(tmp_path: Path):
    """Battery's copy of the defect #127 fixed in combustion (#138).

    Every battery `instruction.md` carries the promise the flame cases do: *"the
    evaluator copies only source files into a clean working copy and strips every
    numeric artifact before re-running, so nothing you leave can affect the
    score."* While `.json` sat on `native_pybamm._REPRODUCTION_KEEP_SUFFIXES` that
    was false: a submission that exported its solved trace through
    `Solution.save_data(..., to_format="json")` shipped that trace straight into
    the reproduction directory.

    Nothing crashes here the way Cantera's `Sim1D.save()` collision did, which is
    why this survived #127: it is the *anti-cheat* end of the same hole rather
    than the false-zero end. A driver that reads back its own exported JSON
    reproduces perfectly without solving, and the strip that exists to prevent
    exactly that was the code being bypassed. `.pkl`, `.mat` and `.csv` -- the
    other three formats `save_data` documents -- were stripped correctly, so
    which idiom the agent happened to pick decided whether the gate applied to
    it.

    The assertion is on which files survived rather than on the keep-list, so
    moving the decision elsewhere (a content read, a different container) does
    not quietly retire it.
    """
    sub = _battery_submission(tmp_path)
    work = tmp_path / "work"

    out = nb._reproduce(sub, work, _battery_spec(), tmp_path / "logs")

    assert out["exit_code"] == 0
    assert "solution_data.json" in out["stripped_files"]
    assert not (work / "solution_data.json").exists()


def test_every_documented_pybamm_export_format_is_stripped(tmp_path: Path):
    """The list `Solution.save_data` accepts, walked end to end.

    `to_format` takes {pickle, matlab, csv, json} and they land on `.pkl`,
    `.mat`, `.csv` and `.json`; `Solution.save` adds a `.pkl`. The bug was that
    three of the four were stripped and one was not, so the gate applied or did
    not depending on which idiom the agent reached for. Asserting the whole set
    is what makes that non-recurrable -- a future keep-list edit has to fail this
    before it can reopen the hole under a different suffix.
    """
    sub = tmp_path / "submission"
    sub.mkdir()
    (sub / "run_case.py").write_text(BATTERY_DRIVER, encoding="utf-8")
    exports = {
        "solution_data.json": PYBAMM_SAVED_SOLUTION_JSON.encode(),
        "solution_data.csv": b"Time [s],Current [A],Voltage [V]\n0.0,5.0,4.0363\n",
        "solution_data.pkl": b"\x80\x04pickled-solution",
        "solution_data.mat": b"MATLAB 5.0 MAT-file",
    }
    for name, blob in exports.items():
        (sub / name).write_bytes(blob)
    work = tmp_path / "work"

    out = nb._reproduce(sub, work, _battery_spec(), tmp_path / "logs")

    assert set(exports) <= set(out["stripped_files"])
    assert not [p for p in exports if (work / p).exists()]


def test_the_source_a_multi_file_driver_is_made_of_still_crosses(tmp_path: Path):
    """The other side of the tightening: nothing legitimate stops working.

    A driver split across modules and subpackages is a legal way to write the
    submission, and the reproduction re-runs `run_case.py` in the copy -- so if
    the strip took its imports with it, a numerically correct submission would
    fail on `ImportError` and score zero. The (e) clause is exactly about that
    band, and it is the reason the keep-list is cut to source rather than to
    nothing.

    What the tightening *does* cost is pinned by the test above it: a data file
    the driver reads back does not survive. That cost is the deal #127 booked
    for combustion, and no battery case pays it -- `solve.sh` copies
    `run_case.py` and nothing else, PyBaMM's parameter sets ship inside the
    wheel, and the battery image installs no `bpx`, so the one JSON *input*
    PyBaMM can read is unavailable in-container.
    """
    sub = tmp_path / "submission"
    (sub / "cell").mkdir(parents=True)
    (sub / "run_case.py").write_text(
        "from cell.trace import write\n\nwrite()\n", encoding="utf-8")
    (sub / "cell" / "__init__.py").write_text("", encoding="utf-8")
    (sub / "cell" / "trace.py").write_text(BATTERY_TRACE_MODULE, encoding="utf-8")
    work = tmp_path / "work"

    out = nb._reproduce(sub, work, _battery_spec(), tmp_path / "logs")

    assert out["exit_code"] == 0
    assert (work / "cell" / "trace.py").is_file()
    assert (work / "results.csv").read_text(encoding="utf-8").count("\n") > 2


def test_the_battery_reproduction_strips_the_formats_it_does_recognise(tmp_path: Path):
    """The half of the strip that works, pinned so fixing the other half cannot
    quietly break it.

    A `.pkl` from `Solution.save()`, the shipped `results.csv` and the figure are
    all artefacts of the graded run and none may survive; `run_case.py` is the
    input and must.
    """
    sub = _battery_submission(tmp_path)
    work = tmp_path / "work"

    out = nb._reproduce(sub, work, _battery_spec(), tmp_path / "logs")

    assert {"sim.pkl", "results.csv", "discharge.png"} <= set(out["stripped_files"])
    assert (work / "run_case.py").is_file()
    # The interface file present afterwards was written by this run, not shipped.
    assert (work / "results.csv").read_text(encoding="utf-8").count("\n") > 2


BATTERY_HEADERS = {
    "as-written-in-the-prompt": ["time_s", "current_A", "voltage_V"],
    "pybamm-variable-names": ["Time [s]", "Current [A]", "Terminal voltage [V]"],
    "terse": ["t", "i", "v"],
    "extra-diagnostic-columns": ["time_s", "current_A", "voltage_V",
                                 "temperature_K", "soc"],
    "diagnostics-before-the-required-columns": ["internal_resistance_ohm",
                                                "temperature_K", "time_s",
                                                "current_A", "voltage_V"],
}


@pytest.mark.parametrize("header", list(BATTERY_HEADERS.values()),
                         ids=list(BATTERY_HEADERS))
def test_a_legal_way_of_spelling_the_columns_still_extracts(header: list[str]):
    """A different but permitted spelling is named in the (e) clause, and it is
    the cheapest way to turn a right answer into a zero.

    `pick_column` matches case-insensitively, ignores bracketing and underscores,
    and falls back to a prefix scan over whatever the submission wrote. The
    prefix scan is the part worth a test: its candidates include the single
    letters `t`, `i` and `v`, and it walks the columns in the file's own order,
    so a submission that puts `temperature_K` or `internal_resistance_ohm` ahead
    of the required three is asking it to resolve a collision. It resolves them
    today because the exact match is tried first; nothing in the code says so,
    which is why this is asserted rather than assumed.
    """
    n = 40
    dt = 90.0
    current = 4.35
    series = {
        "time": [i * dt for i in range(n)],
        "current": [current] * n,
        "voltage": [4.15 - 0.0022 * i for i in range(n)],
        "temperature_K": [298.15 + 0.02 * i for i in range(n)],
        "soc": [1.0 - i / n for i in range(n)],
        "internal_resistance_ohm": [0.031] * n,
    }
    role = {"time_s": "time", "Time [s]": "time", "t": "time",
            "current_A": "current", "Current [A]": "current", "i": "current",
            "voltage_V": "voltage", "Terminal voltage [V]": "voltage", "v": "voltage"}
    cols = {name: series[role.get(name, name)] for name in header}

    out = nb.extract_discharge(cols, _battery_spec())

    assert out["discharge_capacity_Ah"] == pytest.approx(
        current * (n - 1) * dt / 3600.0, rel=1e-9)
    assert out["n_points"] == n


def test_a_header_the_evaluator_cannot_read_refuses_instead_of_returning_a_number():
    """`numpy.savetxt(..., header=...)` comments the header out by default.

    `read_results_csv` drops `#` lines as comments, so that header becomes a
    comment and the first data row becomes the header. #68's second fault is why
    this is worth an assertion: the failure mode that costs the most is not a
    refusal, it is a *confident wrong number*. The refusal names the columns it
    looked for and the ones it found, so a trial log can tell "the agent could
    not do the physics" from "the agent wrote the file differently".
    """
    n = 40
    cols = {"0.000000000000000000e+00": [i * 90.0 for i in range(n)],
            "4.350000000000000000e+00": [4.35] * n,
            "4.150000000000000000e+00": [4.15 - 0.0022 * i for i in range(n)]}

    with pytest.raises(RuntimeError, match="no column matching"):
        nb.extract_discharge(cols, _battery_spec())


def test_a_coarse_output_grid_is_refused_by_name_and_the_threshold_is_visible():
    """A coarser output grid is legal under the interface and undeclared in it.

    `_require_span` refuses below 20 samples, and that threshold appears nowhere
    in `instruction.md` -- the same class of undeclared gate as #88's
    reproduction budget. It is pinned rather than changed here: moving it is a
    scoring-contract decision. What a test can honestly assert is that the
    refusal is explicit, names the count, and stops exactly at the boundary the
    code documents instead of drifting into a range where a correct submission
    scores wrong.
    """
    current = 4.35

    def trace(n: int) -> dict[str, list[float]]:
        dt = 3600.0 / (n - 1)
        return {"time_s": [i * dt for i in range(n)],
                "current_A": [current] * n,
                "voltage_V": [4.15 - 0.0022 * i for i in range(n)]}

    with pytest.raises(RuntimeError, match="only 19 points"):
        nb.extract_discharge(trace(19), _battery_spec())

    out = nb.extract_discharge(trace(20), _battery_spec())
    assert out["discharge_capacity_Ah"] == pytest.approx(current, rel=1e-9)


# ── battery — "a rate-capability trace written differently" (#406) ──────


def _rate_capability_trace(*, dt: float, rest_rows: int, decimals: int | None,
                           low_A: float, high_A: float, n: int) -> str:
    """A CSV holding two discharges of the same cell with a recharge between.

    The KPI is the relative charge the second delivers less than the first, so
    the numbers below are chosen to make that exactly 20%: the low-rate leg
    runs `n` samples at `low_A`, the high-rate leg `n` samples at `high_A`, and
    `high_A * n * dt` is 80% of `low_A * n * dt`.
    """
    rows = []
    t = 0.0

    def emit(current, voltage):
        nonlocal t
        v = round(voltage, decimals) if decimals is not None else voltage
        rows.append(f"{t},{current},{v}")
        t += dt

    for i in range(n):
        emit(low_A, 4.15 - 0.0016 * i)
    for _ in range(rest_rows):          # a rest the contract never forbids
        emit(0.0, 2.55)
    for i in range(n):                  # the CC-CV recharge, negative current
        emit(-low_A, 2.6 + 0.0016 * i)
    for _ in range(rest_rows):
        emit(0.0, 4.19)
    for i in range(n):
        emit(high_A, 4.14 - 0.0018 * i)
    return "time_s,current_A,voltage_V\n" + "\n".join(rows) + "\n"


def test_a_rate_capability_trace_scores_the_same_however_it_is_laid_out():
    """Verification (e) for the KPI #406 put on the four battery cases.

    The reported quantity is a RELATION between two legs of one trace, which is
    a shape `extract_discharge` never had: the extractor has to find the legs
    itself, off the sign of the current, with no help from the contract about
    where they start. Four legal differences that must not move the answer --
    a rest inserted between the legs, a coarser sample spacing, a voltage
    column rounded for reporting, and a trailing rest after the last leg -- are
    exactly the ways a real submission differs from the oracle, and each of
    them changes the row indices every segment boundary sits at.

    20% by construction, so the assertion is on a number the test states rather
    than on whatever the code happens to return.
    """
    spec = nb.PyBaMMSpec(case_id="variant", kind="rate_capability",
                         parameter_set="Chen2020", initial_soc=1.0)
    variants = {
        "oracle-like": dict(dt=30.0, rest_rows=0, decimals=None),
        "rest between the legs": dict(dt=30.0, rest_rows=8, decimals=None),
        "coarser output grid": dict(dt=120.0, rest_rows=0, decimals=None),
        "voltage rounded to 3 dp": dict(dt=30.0, rest_rows=4, decimals=3),
    }
    for name, kw in variants.items():
        # Through the file reader the evaluator itself uses, rather than a
        # second parser written here: the point of (e) is to drive the real
        # chain, and a test-local parser is a way to pass while the real one
        # would not.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "results.csv"
            p.write_text(_rate_capability_trace(low_A=5.0, high_A=4.0, n=40, **kw),
                         encoding="utf-8", newline="\n")
            cols = nb.read_results_csv(p)
        out = nb.extract_rate_capability(cols, spec)
        assert out["n_discharge_segments"] == 2, name
        assert out["rate_capacity_loss_pct"] == pytest.approx(20.0, rel=2e-3), name


def test_a_rate_capability_trace_with_only_one_discharge_is_refused():
    """The other direction. A submission that ran one rate and stopped has no
    relation to report, and the evaluator has to say so rather than divide by
    whatever the segmentation happened to find."""
    spec = nb.PyBaMMSpec(case_id="variant", kind="rate_capability",
                         parameter_set="Chen2020", initial_soc=1.0)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "results.csv"
        rows = "\n".join(f"{i * 30.0},5.0,{4.15 - 0.0016 * i}" for i in range(60))
        p.write_text("time_s,current_A,voltage_V\n" + rows + "\n",
                     encoding="utf-8", newline="\n")
        cols = nb.read_results_csv(p)
    with pytest.raises(RuntimeError, match="1 discharge segment"):
        nb.extract_rate_capability(cols, spec)


# ── combustion — "two transport closures, written differently" (#406) ────


def _flame_profile(su_m_s: float, *, n: int, burned_first: bool,
                   headers: tuple[str, str, str]) -> str:
    """One flame profile on the three-column interface, `su_m_s` at the inlet."""
    xs = [i * 3.0e-4 for i in range(n)]
    temps = [300.0 + 1900.0 * (i / (n - 1)) for i in range(n)]
    us = [su_m_s * (1.0 + 5.0 * (i / (n - 1))) for i in range(n)]
    rows = list(zip(xs, temps, us))
    if burned_first:
        # Same solution, stored from the burned side. `x` is re-run forward so
        # the file is a legal profile rather than a reversed listing, which is
        # what a submission that integrated the other way actually writes.
        rows = [(x, T, u) for x, (_, T, u) in zip(xs, reversed(rows))]
    body = "\n".join(f"{x},{T},{u}" for x, T, u in rows)
    return ",".join(headers) + "\n" + body + "\n"


def test_the_transport_ratio_reads_the_same_from_either_profile_orientation(
    tmp_path: Path,
):
    """Verification (e) for the KPI #406 put on the seven flame cases.

    The graded number is Su(second closure) / Su(reference), and each Su is the
    velocity at the *unburned* edge of its own profile. So the one thing a
    legal submission can differ in that this KPI is exposed to is which end of
    the domain each file starts at -- and the two files need not agree with
    each other. Four combinations, one answer, plus a different column spelling
    and a different row count on the second file so the two profiles are not
    secretly required to share a grid.
    """
    spec = nc.CanteraSpec(case_id="variant", kind="transport_ratio",
                          mechanism="gri30.yaml", fuel="CH4", phi=1.07,
                          T0_K=312.0, P0_atm=1.2,
                          second_profile="results_unity_lewis.csv")
    plain = ("grid_m", "T_K", "velocity_m_s")
    spelled = ("x (m)", "T (K)", "velocity (m/s)")
    for ref_burned_first in (False, True):
        for alt_burned_first in (False, True):
            ref = nc.read_results_csv(_write(
                tmp_path, f"ref-{ref_burned_first}-{alt_burned_first}.csv",
                _flame_profile(0.40, n=60, burned_first=ref_burned_first,
                               headers=plain)))
            alt = nc.read_results_csv(_write(
                tmp_path, f"alt-{ref_burned_first}-{alt_burned_first}.csv",
                _flame_profile(0.30, n=95, burned_first=alt_burned_first,
                               headers=spelled)))
            out = nc.extract_transport_ratio(ref, spec, alt)
            assert out["unity_lewis_speed_ratio"] == pytest.approx(0.75, rel=1e-9)
            assert out["flame_speed_cm_s"] == pytest.approx(40.0, rel=1e-9)
            assert out["unity_lewis_speed_cm_s"] == pytest.approx(30.0, rel=1e-9)


def test_a_transport_ratio_without_its_second_profile_fails_extraction(
    tmp_path: Path,
):
    """The gate this KPI relies on, in the one direction that matters.

    There is no separate dimension for "did the second closure get solved":
    `extraction_succeeded` is already a gate, so a submission that solved once
    and reported a ratio has to fail *there*, with a reason a reader can act
    on, rather than reach the tolerance band with half an answer.
    """
    spec = nc.CanteraSpec(case_id="variant", kind="transport_ratio",
                          mechanism="gri30.yaml", fuel="CH4", phi=1.07,
                          T0_K=312.0, P0_atm=1.2,
                          second_profile="results_unity_lewis.csv")
    ref = nc.read_results_csv(_write(
        tmp_path, "ref-only.csv",
        _flame_profile(0.40, n=60, burned_first=False,
                       headers=("grid_m", "T_K", "velocity_m_s"))))

    with pytest.raises(RuntimeError, match="results_unity_lewis.csv"):
        nc.extract_transport_ratio(ref, spec, None)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


# ── packaging — calculix_interface ──────────────────────────────────────


def test_a_packaging_submission_that_lays_itself_out_differently_keeps_its_inputs(
    tmp_path: Path,
):
    """"Different intermediate filenames and directory layout" is the (e) clause
    the strip list decides.

    Two ways this can zero a right answer, and one way it can pass a wrong one:
    strip an input the rerun needs and the rerun dies; keep a result and the
    rerun echoes it. So a submission is built here the way a CalculiX user
    actually would -- a pre-generated mesh, an include deck, a Python
    pre-processor, results scattered in a subdirectory -- and both halves are
    asserted at once, on `strip_generated` itself rather than on the tuple it
    reads.
    """
    sub = tmp_path / "submission"
    (sub / "mesh").mkdir(parents=True)
    (sub / "out").mkdir()
    inputs = {
        "run_case.sh": "ccx -i model\n",
        "model.inp": "*HEADING\n",
        "mesh/plate.msh": "$MeshFormat\n",
        "mesh/build_mesh.py": "import gmsh\n",
        "materials.inp": "*MATERIAL, NAME=tim\n",
    }
    artefacts = [
        "model.frd", "model.dat", "model.sta", "model.cvg",
        "out/model.frd", "out/spooles.out", "solve.log", "results.csv",
    ]
    for name, text in inputs.items():
        (sub / name).write_text(text, encoding="utf-8")
    for name in artefacts:
        (sub / name).write_text("0.0\n", encoding="utf-8")

    removed = cx.strip_generated(sub, ("results.csv",))

    assert set(removed) >= {"results.csv", "solve.log"}
    for name in artefacts:
        assert not (sub / name).exists(), f"{name} survived the strip"
    for name in inputs:
        assert (sub / name).is_file(), f"{name} was stripped but it is an input"


# ── cfd — openfoam_interface ────────────────────────────────────────────


def test_an_openfoam_grid_study_keeps_its_numeric_input_directories(tmp_path: Path):
    """The layout an order-of-accuracy case forces, which is where the strip is
    most easily wrong.

    A convergence KPI needs three solves, so the natural submission is three
    case roots under directories named `20/`, `40/`, `80/`. Those names are also
    what a *solved time directory* looks like, and deleting them removes the
    cases rather than their output. `_is_case_root` anchors on `system/` to tell
    them apart; this drives that decision through `strip_generated` with a
    submission shaped like the real thing, so a future simplification of the
    stripper cannot silently start eating the grid study.
    """
    sub = tmp_path / "submission"
    for level in ("20", "40", "80"):
        case = sub / level
        (case / "system").mkdir(parents=True)
        (case / "constant" / "polyMesh").mkdir(parents=True)
        (case / "0").mkdir()
        (case / "system" / "controlDict").write_text("application icoFoam;\n",
                                                     encoding="utf-8")
        (case / "0" / "U").write_text("internalField uniform (0 0 0);\n",
                                      encoding="utf-8")
        (case / "constant" / "polyMesh" / "points").write_text("0\n", encoding="utf-8")
        # what the run leaves behind
        (case / "0.5").mkdir()
        (case / "0.5" / "U").write_text("solved\n", encoding="utf-8")
        (case / "log.icoFoam").write_text("End\n", encoding="utf-8")
        (case / "postProcessing").mkdir()
        (case / "postProcessing" / "probe.dat").write_text("0\n", encoding="utf-8")
    (sub / "Allrun").write_text("#!/bin/sh\n", encoding="utf-8")
    (sub / "grid_convergence.csv").write_text("h,l2_error\n", encoding="utf-8")

    removed = of.strip_generated(sub, ("grid_convergence.csv",))

    assert "grid_convergence.csv" in removed
    for level in ("20", "40", "80"):
        assert (sub / level / "system" / "controlDict").is_file(), (
            f"the {level}/ case root was deleted; it is an input, not a time directory")
        assert (sub / level / "0" / "U").is_file()
        assert not (sub / level / "0.5").exists()
        assert not (sub / level / "log.icoFoam").exists()
        assert not (sub / level / "postProcessing").exists()
        assert not (sub / level / "constant" / "polyMesh").exists()


def test_a_grid_study_survives_when_the_submission_root_is_itself_a_case(
    tmp_path: Path,
):
    """The same layout as the test above, plus the one thing the prompt requires.

    `test_an_openfoam_grid_study_keeps_its_numeric_input_directories` builds a
    submission whose root holds only `Allrun` and the CSV, so the root is not a
    case root and its children are never scanned. Every cfd prompt, however,
    also requires `0/`, `constant/` and `system/` in the submission root -- and
    THAT is what makes the root a case root, puts its children through the
    time-directory test, and deletes `20/`, `40/`, `80/` as if they were solver
    output.

    Measured on a real submission (#199): numerically identical to the oracle to
    ten significant figures, scored **0.0**, with `failure_category` reading
    `evaluator_error` rather than anything about the physics. The crash came one
    step after the deletion -- the case list is built before any removal, so the
    loop reached `case/20` having just deleted it.

    A directory that is itself a case root is a refinement level. A real time
    directory has no `system/`, so the strict reading costs the stripper nothing.
    """
    sub = tmp_path / "submission"
    # what every cfd prompt requires of the submission root
    for item in ("0", "constant", "system"):
        (sub / item).mkdir(parents=True)
    (sub / "system" / "controlDict").write_text("application simpleFoam;\n",
                                                encoding="utf-8")
    (sub / "0" / "U").write_text("internalField uniform (0 0 0);\n", encoding="utf-8")
    (sub / "Allrun").write_text("#!/bin/sh\n", encoding="utf-8")
    (sub / "grid_convergence.csv").write_text("h,l2_error\n", encoding="utf-8")

    for level in ("20", "40", "80"):
        case = sub / level
        (case / "system").mkdir(parents=True)
        (case / "0").mkdir()
        (case / "constant" / "polyMesh").mkdir(parents=True)
        (case / "system" / "controlDict").write_text("application simpleFoam;\n",
                                                     encoding="utf-8")
        (case / "0" / "U").write_text("internalField uniform (0 0 0);\n",
                                      encoding="utf-8")
        (case / "constant" / "polyMesh" / "points").write_text("0\n", encoding="utf-8")
        (case / "1000").mkdir()
        (case / "1000" / "U").write_text("solved\n", encoding="utf-8")
        (case / "log.simpleFoam").write_text("End\n", encoding="utf-8")

    removed = of.strip_generated(sub, ("grid_convergence.csv",))

    for level in ("20", "40", "80"):
        assert (sub / level / "system" / "controlDict").is_file(), (
            f"level {level}/ was deleted as a time directory; it is a case root, "
            f"and the rerun cannot regenerate what the submission is made of")
        assert (sub / level / "0" / "U").is_file()
        # its own generated state still goes
        assert not (sub / level / "1000").exists()
        assert not (sub / level / "log.simpleFoam").exists()
        assert not (sub / level / "constant" / "polyMesh").exists()
    assert "grid_convergence.csv" in removed


def test_a_numeric_time_directory_is_still_stripped_next_to_a_numeric_level(
    tmp_path: Path,
):
    """The other half: the guard must not become "never strip a number".

    A real solved time directory has no `system/`. Both shapes sit in one
    submission here so the distinction is asserted rather than assumed.
    """
    sub = tmp_path / "submission"
    (sub / "system").mkdir(parents=True)
    (sub / "system" / "controlDict").write_text("application simpleFoam;\n",
                                                encoding="utf-8")
    (sub / "constant" / "polyMesh").mkdir(parents=True)
    (sub / "constant" / "polyMesh" / "points").write_text("0\n", encoding="utf-8")
    (sub / "0").mkdir()
    # a refinement level: has system/
    (sub / "40" / "system").mkdir(parents=True)
    (sub / "40" / "system" / "controlDict").write_text("application simpleFoam;\n",
                                                       encoding="utf-8")
    # a solved time directory of the ROOT case: no system/
    (sub / "500").mkdir()
    (sub / "500" / "U").write_text("solved\n", encoding="utf-8")

    removed = of.strip_generated(sub, ("grid_convergence.csv",))

    assert (sub / "40" / "system" / "controlDict").is_file()
    assert not (sub / "500").exists(), "a real time directory must still be stripped"
    assert "500" in removed


def test_a_task_supplied_mesh_survives_the_strip_that_removes_a_generated_one(
    tmp_path: Path,
):
    """A published grid handed to the agent in `environment/` is an input.

    `naca0012_subsonic` scores against a reference computed on NASA's own grid,
    so the case ships that grid and the submission must not be asked to
    regenerate it. `preserve` is how a case says so, and getting it wrong breaks
    the rerun outright rather than scoring it low -- the failure looks like the
    agent's.
    """
    sub = tmp_path / "submission"
    (sub / "system").mkdir(parents=True)
    (sub / "constant" / "polyMesh").mkdir(parents=True)
    (sub / "system" / "controlDict").write_text("application simpleFoam;\n",
                                                encoding="utf-8")
    (sub / "constant" / "polyMesh" / "points").write_text("0\n", encoding="utf-8")
    (sub / "500").mkdir()
    (sub / "500" / "U").write_text("solved\n", encoding="utf-8")

    removed = of.strip_generated(sub, ("wall_profile.csv",),
                                 preserve=("constant/polyMesh",))

    assert "500" in removed
    assert (sub / "constant" / "polyMesh" / "points").is_file()
    assert not (sub / "500").exists()


def test_a_results_file_whose_header_row_is_missing_is_still_read(tmp_path: Path):
    """The extraction both interface tracks share, and a measured (e)-band zero.

    `read_interface`'s own comment records it: a submission whose three
    refinement levels agreed with the reference to eight figures scored zero
    because its first line was the data rather than the column names. Recovery
    is admissible only when every field on that line parses as a number *and*
    the count matches the declared columns, so the guard is asserted from both
    sides -- recovered when unambiguous, refused by name when not.
    """
    good = tmp_path / "grid_convergence.csv"
    good.write_text("20,0.05,0.00371611432368\n40,0.025,0.000928\n80,0.0125,0.000232\n",
                    encoding="utf-8")

    cols = read_interface_columns(good)

    assert cols["h"] == [0.05, 0.025, 0.0125]
    assert cols["l2_error"][0] == pytest.approx(0.00371611432368)

    ambiguous = tmp_path / "ambiguous.csv"
    ambiguous.write_text("n,h,error\n20,0.05,0.0037\n40,0.025,0.000928\n80,0.0125,0.0002\n",
                         encoding="utf-8")
    with pytest.raises(EvaluationFailure, match="missing required column"):
        read_interface_columns(ambiguous)


def _poiseuille_contract() -> tuple[dict, dict]:
    """The real `spec.json` + `kpis.json` of plane_poiseuille_friction_factor.

    Read from the case rather than restated here, so a later edit to its band,
    its columns or its derivation is what these assertions run against.
    """
    import json

    case = _repo_root() / "cases" / "cfd" / "fluids" / "plane_poiseuille_friction_factor"
    spec = json.loads((case / "tests" / "spec.json").read_text(encoding="utf-8"))
    kpis = json.loads((case / "tests" / "kpis.json").read_text(encoding="utf-8"))["kpis"]
    return spec, kpis


# The oracle's own three levels, measured on the domain image (#199).
POISEUILLE_ORACLE_ROWS = (
    (20, 0.05, 2.556256e-03),
    (40, 0.025, 6.400915e-04),
    (80, 0.0125, 1.600872e-04),
)


def _score_poiseuille(tmp_path: Path, rows) -> tuple[float, dict]:
    """Drive the real scoring chain over a `grid_convergence.csv` holding `rows`."""
    from sim_benchmark_verifier.csv_interface import read_interface, score_kpis

    spec, kpis = _poiseuille_contract()
    path = tmp_path / spec["interface"]["file"]
    path.write_text(
        "n_cells_wall_normal,h,l2_error\n"
        + "".join(f"{n},{h!r},{e!r}\n" for n, h, e in rows),
        encoding="utf-8",
    )
    cols = read_interface(path, list(spec["interface"]["columns"]),
                          int(spec["interface"]["min_rows"]))
    return score_kpis(cols, spec["kpis"], kpis)


@pytest.mark.parametrize(
    "name,rows",
    [
        ("oracle", POISEUILLE_ORACLE_ROWS),
        # The property the whole case rests on: an ORDER is invariant to any
        # positive constant multiplying the norm. The prompt fixes the norm's
        # definition, but a submission is free to reach it by a route that
        # differs by a constant -- a different quadrature for the bulk mean, the
        # sum not divided by n, a percentage instead of a fraction -- and none of
        # those is a wrong answer. If this ever stopped holding, the case would
        # be scoring the agent's arithmetic conventions.
        ("scaled by 7.3", tuple((n, h, e * 7.3) for n, h, e in POISEUILLE_ORACLE_ROWS)),
        ("scaled by 1/500", tuple((n, h, e / 500.0) for n, h, e in POISEUILLE_ORACLE_ROWS)),
        # Fine-to-coarse. A least-squares slope does not care, but nothing in the
        # prompt tells the agent which way round to write the rows.
        ("rows reversed", tuple(reversed(POISEUILLE_ORACLE_ROWS))),
        # Four levels instead of three. `min_rows` is a floor, and an agent that
        # adds a grid has done more work, not less.
        ("four levels", POISEUILLE_ORACLE_ROWS + ((160, 0.00625, 4.002e-05),)),
    ],
)
def test_a_correct_poiseuille_refinement_study_scores_however_it_is_written(
    tmp_path: Path, name: str, rows,
):
    """Verification (e) for the case switched to an observed order in #199.

    (c) and (d) are the oracle and a wreck; this is the band between them, and
    for an order-of-accuracy KPI the band is unusually wide because the scored
    quantity is a *relation*. Every row set below is the same correct physics
    written a legal other way, and every one has to score exactly 1.0.

    Exactly, not `>= 0.9` (#359): the band is binary, so a fraction here would
    mean a whole KPI group scored nothing on an answer that is right -- which is
    the defect class (e) exists to find, not a near miss to wave through. The
    order-of-accuracy shape is where that is least obvious, because a legal
    alternate route really can move the *fitted* number; what absorbs that is
    the band, and `band_pass` on either side of it is still 1 or 0.
    """
    total, per_kpi = _score_poiseuille(tmp_path, rows)

    assert total == pytest.approx(1.0), f"{name}: correct study scored {total}"
    assert per_kpi["observed_order_u"]["value"] == pytest.approx(2.0, abs=0.05), name


def test_a_first_order_poiseuille_study_scores_zero(tmp_path: Path):
    """Verification (d) at the scoring layer: the band still separates 1 from 2.

    `pass_tol = 0.25` is absolute for exactly this reason, so the check that it
    is doing its job is that a genuinely first-order study -- error halving as
    the grid halves -- scores nothing.
    """
    rows = tuple((n, h, 1.0e-3 * (h / 0.05)) for n, h, _ in POISEUILLE_ORACLE_ROWS)

    total, per_kpi = _score_poiseuille(tmp_path, rows)

    assert per_kpi["observed_order_u"]["value"] == pytest.approx(1.0, abs=1e-6)
    assert total == 0.0


def test_the_retired_poiseuille_kpi_is_recorded_and_cannot_reach_the_score(
    tmp_path: Path,
):
    """`f_times_re` is kept at weight 0 (#199), and "weight 0" has to be real.

    `score_kpis` derives its group weights as `1 / len(groups)` and ignores the
    `weight` a `kpis.json` declares, so a retired KPI that were still derived by
    `spec.json` would quietly take HALF the score rather than none of it. What
    actually retires it is its absence from `spec.json`; the weight-0 group is
    documentation. Both halves are asserted here because only one of them is
    load-bearing and it is not the obvious one.
    """
    spec, kpis = _poiseuille_contract()

    assert "f_times_re" in kpis, (
        "the retired KPI was deleted; 23 stored rows were scored against it and "
        "lose their definition")
    assert kpis["f_times_re"]["group"] == "diagnostic"
    assert "f_times_re" not in spec["kpis"], (
        "the retired KPI is derived again, which under score_kpis' equal-weight "
        "groups gives the recallable closed form half the score")

    _, per_kpi = _score_poiseuille(tmp_path, POISEUILLE_ORACLE_ROWS)

    assert set(per_kpi) == {"observed_order_u"}


def read_interface_columns(path: Path) -> dict[str, list[float]]:
    """`read_interface` under the column set an order-of-accuracy case declares."""
    from sim_benchmark_verifier.csv_interface import read_interface

    return read_interface(path, ["n", "h", "l2_error"], min_rows=3)


# ── a KPI between two configurations — the shape shared by three cases ──


# Measured, not invented: `tools/pkg25d_oracle.py`'s own thermal solve of the
# live 2.5D package (k = 4.7 W/m/K, h_lid = 22600, 214 W on the ASIC) at four
# in-plane grid spacings, with the TIM thickness as the one thing changed. The
# numbers are frozen here because they are what the assertions below decided,
# not a description of anything that moves.
#                       h [mm]:      1.4        1.2        1.0        0.7
PKG_T_ASIC_MAX_C = {
    "baseline": (99.69432, 99.46909, 99.42533, 99.45654),   # TIM 0.118 mm
    "modified": (84.55194, 84.37681, 84.33802, 84.34279),   # TIM 0.060 mm
    "near":     (95.16666, 94.95501, 94.91054, 94.93098),   # TIM 0.100 mm
}
PKG_GRIDS = (1.4, 1.2, 1.0, 0.7)


def _pair_contract(kind: str, gt: float, a: str = "baseline", b: str = "modified"):
    """A `spec.json` + `kpis.json` pair for the relation `kind` between `a` and `b`.

    Written here rather than read from a case because #460 adds the capability
    and lands no case: the three issues that will use it (#410 (2), #453, #459)
    each decide their own operating point. The shapes are the real ones -- the
    same keys `score_kpis` reads on every live track, and a flat 5% band.
    """
    spec = {
        "interface": {"file": "results.csv", "columns": ["t_asic_max_c"],
                      "labels": ["config"], "min_rows": 2},
        "kpis": {"relation": {"derive": kind, "key": "config",
                              "a": a, "b": b, "value": "t_asic_max_c"}},
    }
    kpis = {"relation": {
        "group": "outputs", "gt_value": gt,
        "physics_min": -1.0e3, "physics_max": 1.0e3,
        "pass_tol": 0.05 * abs(gt), "gross_error_tol": 0.15 * abs(gt),
    }}
    return spec, kpis


def _score_pair(tmp_path: Path, spec: dict, kpis: dict, text: str):
    """Drive the real chain -- `read_interface` then `score_kpis` -- over `text`."""
    from sim_benchmark_verifier.csv_interface import read_interface, score_kpis

    interface = spec["interface"]
    path = tmp_path / interface["file"]
    path.write_text(text, encoding="utf-8")
    cols = read_interface(path, list(interface["columns"]),
                          int(interface["min_rows"]), list(interface["labels"]))
    return score_kpis(cols, spec["kpis"], kpis)


def _rows(*pairs: tuple[str, float], columns=("config", "t_asic_max_c")) -> str:
    return ",".join(columns) + "\n" + "".join(
        "%s,%r\n" % (label, value) for label, value in pairs)


PKG_RATIO_GT = 0.848169        # mean of modified/baseline over the four grids
PKG_DELTA_GT = -15.108930      # mean of modified-baseline, kelvin
PKG_NEAR_RATIO_GT = 0.954573   # the same relation with a smaller change


@pytest.mark.parametrize("kind,gt", [("pair_ratio", PKG_RATIO_GT),
                                     ("pair_delta", PKG_DELTA_GT)])
@pytest.mark.parametrize("name,text", [
    # The oracle's own shape, at the grid it calibrates on.
    ("as the oracle writes it",
     _rows(("baseline", 99.42533), ("modified", 84.33802))),
    # A design sweep that happens to contain the two named rows. #453's task
    # asks for exactly this: the agent explores, and two rows of the file are
    # the ones the prompt pinned.
    ("two named rows inside a wider sweep",
     _rows(("trial_a", 91.2), ("baseline", 99.42533), ("trial_b", 88.7),
           ("modified", 84.33802), ("trial_c", 79.9))),
    # Nothing in the prompt says which row comes first.
    ("rows in the other order",
     _rows(("modified", 84.33802), ("baseline", 99.42533))),
    # Capitalisation and padding are not physics.
    ("labels spelled differently",
     _rows((" Baseline", 99.42533), ("MODIFIED ", 84.33802))),
    # A finer grid than the oracle's. The same physics, resolved better -- and
    # the reason a relation is worth having is that this must not move it.
    ("both configurations on a finer grid",
     _rows(("baseline", 99.45654), ("modified", 84.34279))),
    # A coarser one.
    ("both configurations on a coarser grid",
     _rows(("baseline", 99.69432), ("modified", 84.55194))),
    # Columns the interface does not declare are the agent's business.
    ("extra columns the spec does not read",
     "config,tim_mm,t_asic_max_c,t_hbm_w_max_c\n"
     "baseline,0.118,99.42533,40.61384\nmodified,0.06,84.33802,38.81013\n"),
])
def test_a_correct_two_configuration_submission_scores_however_it_is_written(
    tmp_path: Path, kind: str, gt: float, name: str, text: str,
):
    """Verification (e) for the relation shape, on both of its derivations.

    (c) and (d) are the oracle and a wreck; this is the band between them, and
    for a relation KPI the band is where the shape's own failure modes live --
    which of two rows is the reference, how a row is identified, and whether a
    submission that resolved the problem better than the oracle still scores.
    Every arm below is the same correct physics written a legal other way and
    has to score exactly 1.0 (#359): a fraction would mean a whole KPI group
    scored nothing on a right answer.
    """
    spec, kpis = _pair_contract(kind, gt)

    total, per_kpi = _score_pair(tmp_path, spec, kpis, text)

    assert total == pytest.approx(1.0), "%s / %s scored %s" % (kind, name, total)
    assert per_kpi["relation"]["score"] == 1.0


# What a submission that never applied the change actually writes. Not
# synthesised: `tools/pkg25d_oracle.py` was run twice at 1.0 mm with the TIM
# thickness taken from neither configuration -- two real CalculiX solves, 5.1 s
# and 5.5 s -- and the two peaks agreed to every digit the interface carries.
# The distinction matters because #410 (2) reached its null the same way, by
# leaving a case unrepaired rather than by writing equal columns, and a test
# that only ever sees the synthetic version is testing the synthesis.
PKG_DEGENERATE_CSV = "config,t_asic_max_c\nbaseline,99.42533\nmodified,99.42533\n"


def test_reporting_the_reference_configuration_twice_is_not_an_answer(tmp_path: Path):
    """The null-answer arm, and the one hazard this shape carries into a case.

    #406 built a relation KPI, reported the reference profile twice so the ratio
    was exactly 1.000, and it scored **1.0** on two flames whose closures differ
    by less than the band. #410 (2) hit it again on a different track, solver and
    KPI: a `kOmegaSST / kOmega` ratio of 1.0144 whose band is [0.9637, 1.0651].
    So the arm belongs to every case that uses this derivation -- what decides it
    is where `gt_value` sits, and the line is exact: under a flat 5% band the
    null ratio scores iff `gt` lies inside [1/1.05, 1/0.95] = [0.9524, 1.0526].

    Both sides are asserted from measured numbers rather than argued, because the
    failing side is the one nobody expects: the same package, the same solver,
    the same derivation, and only the *size* of the change moved.
    """
    far, far_kpis = _pair_contract("pair_ratio", PKG_RATIO_GT)
    total, per_kpi = _score_pair(tmp_path, far, far_kpis, PKG_DEGENERATE_CSV)
    assert total == 0.0, "TIM 0.118 -> 0.060 changes the peak by 15%, so 1.0 is not an answer"
    assert per_kpi["relation"]["value"] == pytest.approx(1.0)

    near, near_kpis = _pair_contract("pair_ratio", PKG_NEAR_RATIO_GT)
    total, _ = _score_pair(tmp_path, near, near_kpis, PKG_DEGENERATE_CSV)
    assert total == 1.0, (
        "measured: TIM 0.118 -> 0.100 moves the peak 4.6%, so `no difference` sits "
        "inside a 5% band and the null answer scores -- a pair_ratio case must "
        "check its own gt against [0.9524, 1.0526] before it lands")

    # A delta has no such window. Its null answer is 0.0, and a flat 5% band
    # around any non-zero `gt` excludes it by construction.
    delta, delta_kpis = _pair_contract("pair_delta", PKG_DELTA_GT)
    assert _score_pair(tmp_path, delta, delta_kpis, PKG_DEGENERATE_CSV)[0] == 0.0
    near_delta, near_delta_kpis = _pair_contract(
        "pair_delta", PKG_T_ASIC_MAX_C["near"][2] - PKG_T_ASIC_MAX_C["baseline"][2],
        b="near")
    assert _score_pair(tmp_path, near_delta, near_delta_kpis,
                       PKG_DEGENERATE_CSV.replace("modified", "near"))[0] == 0.0


@pytest.mark.parametrize("kind,gt,scores", [
    # Measured on this repo's own runs. The first two are the packaging pair
    # above; the third and fourth are #410 (2)'s two closure pairs on
    # `channel_retau395_repair_closure`, where the settled one is the one whose
    # answer can be typed.
    ("pair_ratio", PKG_RATIO_GT, False),        # TIM 15% change  -> 3.58 widths
    ("pair_ratio", PKG_NEAR_RATIO_GT, True),    # TIM 4.6% change -> 0.95 widths
    ("pair_ratio", 1.0144, True),               # kOmegaSST/kOmega -> 0.28 widths
    ("pair_ratio", 1.179, False),               # SpalartAllmaras  -> 3.04 widths
    ("pair_delta", PKG_DELTA_GT, False),        # every delta      -> 20 widths
    ("pair_delta", -0.001, False),
])
def test_the_reported_null_margin_agrees_with_what_the_null_actually_scores(
    tmp_path: Path, kind: str, gt: float, scores: bool,
):
    """The number the derivation surfaces, checked against the thing it predicts.

    `null_answer_margin` is arithmetic on `gt_value` and `pass_tol`; whether the
    null answer scores is the whole chain run over a degenerate `results.csv`.
    Asserting them against each other is the point -- computed two independent
    ways, so neither is checking itself (#398's trap, where a negative control
    compared every row of a table against that same row).

    What it buys is that the hazard is visible **when the case is authored**:
    the oracle run the case-PR bar already requires now writes the margin into
    `reward_detail.json`, instead of #406 and #410 (2) each measuring it by hand
    after the case existed.
    """
    from sim_benchmark_verifier.csv_interface import null_answer_margin

    spec, kpis = _pair_contract(kind, gt)
    margin = null_answer_margin(spec["kpis"]["relation"], kpis["relation"])

    assert margin is not None
    assert (margin <= 1.0) is scores

    total, per_kpi = _score_pair(tmp_path, spec, kpis, PKG_DEGENERATE_CSV)

    assert (total == 1.0) is scores, "gt %r: margin says %.4f widths" % (gt, margin)
    assert per_kpi["relation"]["null_answer_margin_band_widths"] == pytest.approx(
        margin, abs=5e-5)


def test_a_delta_is_always_twenty_band_widths_from_its_null_answer(tmp_path: Path):
    """The closed form worth knowing before a case picks its derivation.

    Under the flat 5% band, `pair_delta`'s distance from "nothing changed" is
    `|0 - gt| / (0.05 |gt|)` -- exactly 20, whatever the operating point. That is
    what makes a delta structurally immune to the null answer while a ratio has
    to be checked, and it is the honest counterweight to the ratio cancelling
    common mode far better (0.10x against 1.35x, measured above).
    """
    from sim_benchmark_verifier.csv_interface import null_answer_margin

    for gt in (PKG_DELTA_GT, -0.001, 1e6, 3.25):
        spec, kpis = _pair_contract("pair_delta", gt)
        assert null_answer_margin(
            spec["kpis"]["relation"], kpis["relation"]) == pytest.approx(20.0)

    # And a derivation that is not a relation has no null answer to report.
    assert null_answer_margin({"derive": "single_row", "value": "cd"},
                              {"gt_value": 0.53, "pass_tol": 0.026}) is None


def test_a_diagnostic_may_not_turn_a_scored_submission_into_an_evaluator_error(
    tmp_path: Path,
):
    """The margin is reported beside the verdict and must never replace it.

    A `pass_tol` of zero is a broken `kpis.json`, and the submission still has to
    be judged by the band rather than by the diagnostic's arithmetic: dividing by
    it would raise, and `score_kpis`' caller turns any exception into
    `evaluator_error`, which files a submission's failure as ours.
    """
    from sim_benchmark_verifier.csv_interface import null_answer_margin

    spec, kpis = _pair_contract("pair_ratio", PKG_RATIO_GT)
    kpis["relation"]["pass_tol"] = 0.0

    assert null_answer_margin(spec["kpis"]["relation"], kpis["relation"]) is None

    total, per_kpi = _score_pair(tmp_path, spec, kpis, PKG_DEGENERATE_CSV)

    assert total == 0.0
    assert "null_answer_margin_band_widths" not in per_kpi["relation"]
    assert per_kpi["relation"]["value"] == pytest.approx(1.0)


def test_the_two_configurations_have_to_be_resolved_alike_for_a_delta(tmp_path: Path):
    """What the common-mode argument does and does not buy -- measured.

    A relation is defended on the grounds that both runs share a mesh, so their
    discretisation error cancels. Over the four grids above, solving *both*
    configurations on each in turn, the peak temperature itself moves 0.27%
    while the ratio moves 0.028% (0.10x) and the delta 0.36% (1.35x). The
    cancellation is real, and it is much stronger for the ratio.

    It is a property of the submission's practice, not of the KPI, and the
    evaluator cannot check it: resolving one configuration on the coarsest grid
    and the other on the finest takes the ratio to 0.52% -- still comfortably
    inside a 5% band -- and the delta to 3.2%, and on a *smaller* change to
    11.6%, which is outside it. So a `pair_delta` case is buying a KPI whose
    band assumes like-for-like comparison, and that is worth knowing before the
    band is set rather than after a correct-looking submission scores zero.
    """
    coarse, fine = 0, 3
    mixed = _rows(("baseline", PKG_T_ASIC_MAX_C["baseline"][coarse]),
                  ("near", PKG_T_ASIC_MAX_C["near"][fine]))

    gt = sum(PKG_T_ASIC_MAX_C["near"][i] - PKG_T_ASIC_MAX_C["baseline"][i]
             for i in range(len(PKG_GRIDS))) / len(PKG_GRIDS)
    spec, kpis = _pair_contract("pair_delta", gt, b="near")
    total, per_kpi = _score_pair(tmp_path, spec, kpis, mixed)

    assert abs(per_kpi["relation"]["value"] - gt) / abs(gt) > 0.05
    assert total == 0.0

    ratio_gt = PKG_NEAR_RATIO_GT
    spec, kpis = _pair_contract("pair_ratio", ratio_gt, b="near")
    total, per_kpi = _score_pair(tmp_path, spec, kpis, mixed)

    assert abs(per_kpi["relation"]["value"] - ratio_gt) / abs(ratio_gt) < 0.05
    assert total == 1.0, "the ratio absorbs a grid mismatch the delta does not"


def test_a_two_configuration_submission_that_did_not_solve_both_scores_zero(
    tmp_path: Path,
):
    """Verification (d) for the shape: the wreck is a file missing a row.

    A submission that solved only the configuration it was already given, or
    that wrote its own labels, produces a file with no row the spec can name.
    That has to be a scored zero carrying `extraction_failed` -- the category
    triage reads as "the file was not what the prompt asked for" -- and not an
    exception, which reads as infrastructure having broken.
    """
    spec, kpis = _pair_contract("pair_ratio", PKG_RATIO_GT)

    with pytest.raises(EvaluationFailure) as e:
        _score_pair(tmp_path, spec, kpis,
                    _rows(("baseline", 99.42533), ("my_design", 84.33802)))

    assert e.value.category == "extraction_failed"
    assert "'modified'" in str(e.value)


# ── the institution: every shared evaluator carries a variant ───────────


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _shared_evaluators() -> dict[str, list[str]]:
    """Modules a live case hands its whole verifier to, and which cases do.

    A case's `tests/verify*.py` is eight lines around
    `from sim_benchmark_verifier.<module> import main_from_case`, so that import
    names the code that decides the case's score. Discovering the set this way
    rather than listing it is the point: a list has to be edited when a track is
    added, and the once it is forgotten the newest family in the repo -- the one
    least likely to be right -- is the one left ungated.

    Live tracks only: the leading underscore already marks `_pending` (drafts),
    `_phase2` (out of scope) and `_template` (placeholders), which is the same
    convention the schema lint selects on.
    """
    pattern = re.compile(
        r"from\s+sim_benchmark_verifier\.(\w+)\s+import\s+main_from_case")
    found: dict[str, list[str]] = {}
    for entry in sorted((_repo_root() / "cases").glob("[!_]*/*/*/tests/*.py")):
        for module in pattern.findall(entry.read_text(encoding="utf-8")):
            found.setdefault(module, []).append(entry.parent.parent.name)
    return found


def _modules_this_file_exercises() -> set[str]:
    """Evaluator modules some `test_*` in this file actually calls into.

    Parsed rather than grepped so a module named only in a docstring or a
    comment does not count as covered -- the failure this whole file exists for
    is a check that looks present and is not.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    alias_to_module: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "sim_benchmark_verifier":
            for alias in node.names:
                alias_to_module[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "sim_benchmark_verifier."
        ):
            module = node.module.split(".", 1)[1]
            for alias in node.names:
                alias_to_module[alias.asname or alias.name] = module

    exercised: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and inner.id in alias_to_module:
                exercised.add(alias_to_module[inner.id])
            elif isinstance(inner, ast.ImportFrom) and (inner.module or "").startswith(
                "sim_benchmark_verifier."
            ):
                exercised.add(inner.module.split(".", 1)[1])
    return exercised


def test_every_shared_evaluator_has_a_variant_submission():
    """Verification (e), as a gate rather than as a habit.

    (e) says a case PR must show that a numerically correct submission written
    differently from the oracle still scores. A generated family needs one such
    submission for the whole family, because one module scores every case in it
    -- which is what makes (e) affordable, and also what makes it forgettable:
    the fiftieth case in a family inherits the variant it never had to write,
    and so does the first case of the *next* family, which does not have one.

    That is #93's finding one level up. The blind spot in a template is invisible
    to (c)+(d) however many times they run; a hand-maintained list of tracks is
    invisible the same way. So the list comes from the cases.
    """
    shared = _shared_evaluators()
    assert shared, "found no live case dispatching to a shared evaluator -- " \
                   "the discovery pattern has drifted from how cases are written"

    exercised = _modules_this_file_exercises()
    missing = {
        module: sorted(set(cases))[:3]
        for module, cases in shared.items()
        if module not in exercised
    }
    assert not missing, (
        "these shared evaluators score live cases and no variant submission in "
        f"this file exercises them: {missing}. Verification (e) is not satisfied "
        "for the cases they grade -- add one variant per module, not one per case."
    )


def test_the_discovery_is_reading_the_tracks_that_actually_exist():
    """A guard on the guard: the gate above is only worth its line count while
    its pattern still matches how a case wires its verifier.

    If every case were rewritten to dispatch differently, `_shared_evaluators`
    would return an empty mapping and the gate would pass by finding nothing.
    Naming the four modules that carry the live tracks today is the one place a
    hard-coded list belongs -- not as the gate, but as evidence the gate can see.
    """
    shared = _shared_evaluators()

    assert {"native_cantera", "native_pybamm", "openfoam_interface"} <= set(shared)
    # And each is genuinely shared, which is what makes one variant enough.
    assert all(len(cases) > 1 for cases in shared.values())
