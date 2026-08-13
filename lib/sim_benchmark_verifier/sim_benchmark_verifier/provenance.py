"""Verify that an agent-claimed KPI value actually comes from where it says.

The agent reports each KPI as ``{"value": <number>, "source": {...}}``.
This module re-extracts the value from the claimed source, compares to
the claimed value, and decides whether the source is trustworthy.

Four source kinds are supported:

- ``file_extract``: value lives at a path under the case dir; an
  extractor (a sandboxed shell pipeline of allowed binaries) returns it.
- ``ltspice_log``: value lives in an LTspice ``.log`` file; the verifier
  parses native ``.meas`` output and ``.step`` lines directly.
- ``sim_run_stdout``: value is in the captured stdout of a specific
  ``sim run``; the extractor pulls it out.
- ``sim_run_kpi``: value is in a ``sim run`` record's ``parsed_output``
  dict (set when the agent's solve script printed a JSON line that the
  driver's ``parse_output`` captured).

Anything else (bare number, missing ``source``, unknown ``kind``)
fails and the KPI scores zero with an explanation.

Why this layer exists: the verifier cannot trust agent self-reports.
By forcing the agent to declare provenance and then independently
re-extracting, the agent's freedom shrinks from "any number" to "any
number that survives an audit against the artifact it pointed at".
That bound is meaningful and solver-neutral.
"""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────
# Sandbox: which binaries / forms are allowed in extractor pipelines
# ──────────────────────────────────────────────────────────────────────

# Allowed first words of each pipe stage. Keep tight — agent supplies
# these strings, so anything we permit here can be invoked at verify time.
# python3 is intentionally NOT included — `python3 -c ...` would defeat the
# whole sandbox.
_ALLOWED_BINARIES = {
    "head", "tail", "awk", "sed", "grep", "cut", "tr",
    "sort", "uniq", "wc", "cat", "jq",
}

# Standalone shell tokens that mean "redirect" or "command chain". After
# `shlex.split`, anything inside single/double quotes is a single token —
# so `awk 'NR>1 {print $2}'` parses to ['awk', 'NR>1 {print $2}'] and the
# `>` is buried inside the awk program, not a redirect. By checking only
# *standalone* tokens we don't reject legitimate awk/sed scripts that use
# > / < / ; for their own syntax.
_REDIRECT_TOKENS = {
    ">", ">>", "<", "<<", ">&", "<&",
    "2>", "2>>", "&>", ";", "&", "&&", "||",
}


def _validate_extractor(extract: str) -> str | None:
    """Return None if extractor passes the sandbox check, else a why-string."""
    if not isinstance(extract, str) or not extract.strip():
        return "extractor is empty or not a string"
    # Cheap injection-pattern reject. These have no legitimate use in
    # extractors, so substring-level reject is safe.
    for danger in ("$(", "`"):
        if danger in extract:
            return f"extractor contains command-substitution {danger!r}"

    # Tokenize and inspect each pipe stage.
    for stage in extract.split("|"):
        try:
            tokens = shlex.split(stage)
        except ValueError as e:
            return f"extractor stage {stage!r} unparseable: {e}"
        if not tokens:
            return "extractor has empty pipe stage"
        head = tokens[0]
        if head not in _ALLOWED_BINARIES:
            return f"extractor stage starts with disallowed binary {head!r}"
        # Check for shell-meaningful operators as STANDALONE tokens (i.e.
        # the agent typed them outside any quotes — would actually be
        # parsed by bash as redirect/chain).
        for tok in tokens[1:]:
            if tok in _REDIRECT_TOKENS:
                return f"extractor contains standalone redirect/chain token {tok!r}"
    return None


# ──────────────────────────────────────────────────────────────────────
# Numeric comparison
# ──────────────────────────────────────────────────────────────────────

def _values_match(claimed: float, extracted: str) -> tuple[bool, str]:
    """Compare claimed vs re-extracted value with attribution-grade tolerance.

    Provenance answers "did you actually read this from the file?", not
    "is this engineering-accurate" — that's the tolerance band's job. Two
    layers managing the same number is double-counting, so we keep
    provenance loose (1% relative). At this tolerance:

      - rounding / truncation at any precision passes (5-digit, 3-digit, …)
      - a 100× off-by-column mistake (e.g. wrong field in a CSV) fails
      - a totally-fabricated number (-0.5 vs -0.06) fails

    1% is "is this number from this file at all", not "did you copy it
    digit-for-digit". The downstream tolerance band is what
    constrain numerical accuracy.
    """
    extracted = extracted.strip()
    if not extracted:
        return False, "extractor returned empty output"
    try:
        actual = float(extracted)
    except ValueError:
        numeric_tokens = _NUMBER_RE.findall(extracted)
        if len(numeric_tokens) == 1:
            return _values_match(claimed, numeric_tokens[0])
        return (str(claimed).strip() == extracted), \
               (f"non-numeric extract {extracted!r} != claim {claimed!r}"
                if str(claimed).strip() != extracted else "ok (string match)")
    if not math.isfinite(actual):
        return False, f"extracted value {actual} is not finite"
    diff = abs(claimed - actual)
    tol = max(1e-9, 1e-2 * max(abs(claimed), abs(actual)))
    if diff <= tol:
        return True, "ok"
    return False, f"value mismatch: claim {claimed}, file {actual}, diff {diff:.6g}"


# ──────────────────────────────────────────────────────────────────────
# Source kinds
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    why: str
    extracted: str = ""


def _verify_file_extract(value, source: dict) -> VerifyResult:
    path = source.get("path")
    extract = source.get("extract", "cat")
    if not isinstance(path, str) or not path:
        return VerifyResult(False, "source.path missing or not a string")
    if not os.path.isabs(path):
        return VerifyResult(False, f"source.path must be absolute, got {path!r}")
    full = Path(path)
    if ".." in full.parts:
        return VerifyResult(False, "source.path may not contain '..'")
    if not full.is_file():
        return VerifyResult(False, f"source file not found: {full}")
    err = _validate_extractor(extract)
    if err is not None:
        return VerifyResult(False, f"extractor rejected: {err}")
    try:
        file_text = full.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return VerifyResult(False, f"source file could not be read: {e}")
    try:
        proc = subprocess.run(
            ["bash", "-c", extract],
            input=file_text, capture_output=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
    except subprocess.TimeoutExpired:
        return VerifyResult(False, "extractor timed out (>10s)")
    if proc.returncode != 0:
        return VerifyResult(False, f"extractor exited {proc.returncode}: {proc.stderr.strip()[:120]}")
    ok, why = _values_match(value, proc.stdout)
    return VerifyResult(ok, why, proc.stdout.strip()[:200])


def _validate_absolute_file(path) -> tuple[Path | None, str | None]:
    if not isinstance(path, str) or not path:
        return None, "source.path missing or not a string"
    if not os.path.isabs(path):
        return None, f"source.path must be absolute, got {path!r}"
    full = Path(path)
    if ".." in full.parts:
        return None, "source.path may not contain '..'"
    if not full.is_file():
        return None, f"source file not found: {full}"
    return full, None


def _read_ltspice_log(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace").replace("\x00", "")
    if data.count(b"\x00") > max(1, len(data) // 10):
        return data.decode("utf-16-le", errors="replace").replace("\x00", "")
    try:
        return data.decode("utf-8").replace("\x00", "")
    except UnicodeDecodeError:
        pass
    return data.decode("utf-8", errors="replace").replace("\x00", "")


_COMPLEX_MAG_RE = re.compile(
    r"^\(?\s*(?P<mag>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*dB\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
)


def _number_from_ltspice_token(token: str) -> str | None:
    token = token.strip().strip("(),")
    if not token:
        return None
    complex_match = _COMPLEX_MAG_RE.match(token)
    if complex_match:
        return complex_match.group("mag")
    number_match = _NUMBER_RE.search(token)
    return number_match.group(0) if number_match else None


def _measure_prefers_axis_value(measurement: str) -> bool:
    name = measurement.lower()
    return (
        name.startswith(("f_", "t_"))
        or re.match(r"^[ft]\d", name) is not None
        or name.endswith(("_freq", "_frequency", "_time"))
        or "freq" in name
        or "rise" in name
    )


def _parse_scalar_measure_line(line: str, measurement: str) -> str | None:
    stripped = line.strip()
    if not stripped.lower().startswith(f"{measurement.lower()}:"):
        return None
    rhs = stripped.split("=", 1)[1] if "=" in stripped else stripped.split(":", 1)[1]
    rhs = rhs.strip()
    if " at " in rhs.lower() and _measure_prefers_axis_value(measurement):
        # WHEN-style measures for time/frequency KPIs log the measured axis
        # point after "at"; ordinary gain measures should keep the left value.
        parts = re.split(r"\bat\b", rhs, flags=re.IGNORECASE)
        rhs = parts[-1].strip()
    return _number_from_ltspice_token(rhs)


def _parse_stepped_measure_table(text: str, measurement: str, step: int) -> str | None:
    in_table = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower() == f"measurement: {measurement.lower()}":
            in_table = True
            continue
        if in_table and line.lower().startswith("measurement:"):
            return None
        if not in_table:
            continue
        parts = line.split()
        if not parts or not parts[0].isdigit():
            continue
        if int(parts[0]) != step:
            continue
        if len(parts) < 2:
            return None
        return _number_from_ltspice_token(parts[1])
    return None


def _extract_ltspice_measure(text: str, measurement: str, step: int | None) -> str | None:
    if step is not None:
        return _parse_stepped_measure_table(text, measurement, step)
    for line in text.splitlines():
        value = _parse_scalar_measure_line(line, measurement)
        if value is not None:
            return value
    return None


def _extract_ltspice_step_param(text: str, param: str, step: int) -> str | None:
    matches: list[str] = []
    param_re = re.compile(rf"\b{re.escape(param)}\s*=\s*(?P<value>\S+)", re.IGNORECASE)
    for line in text.splitlines():
        if not line.strip().lower().startswith(".step"):
            continue
        m = param_re.search(line)
        if m:
            value = _number_from_ltspice_token(m.group("value"))
            if value is not None:
                matches.append(value)
    if step > len(matches):
        return None
    return matches[step - 1]


def _verify_ltspice_log(value, source: dict) -> VerifyResult:
    full, err = _validate_absolute_file(source.get("path"))
    if err is not None:
        return VerifyResult(False, err)
    assert full is not None
    query = source.get("query")
    if query not in {"completed", "measure", "step_param"}:
        return VerifyResult(False, "source.query must be 'completed', 'measure', or 'step_param'")
    text = _read_ltspice_log(full)
    if query == "completed":
        has_elapsed = "Total elapsed time" in text
        has_error = bool(re.search(r"(?im)^\s*(?:\*\*\*|error\b)", text))
        extracted = "1" if has_elapsed and not has_error else "0"
        ok, why = _values_match(value, extracted)
        return VerifyResult(ok, why, extracted)

    if query == "step_param":
        param = source.get("param")
        if not isinstance(param, str) or not param.strip():
            return VerifyResult(False, "source.param missing or not a string")
        try:
            step = int(source.get("step"))
        except (TypeError, ValueError):
            return VerifyResult(False, "source.step must be an integer for step_param")
        if step < 1:
            return VerifyResult(False, "source.step must be >= 1")
        extracted = _extract_ltspice_step_param(text, param.strip(), step)
        if extracted is None:
            return VerifyResult(False, f"step param {param!r} step {step} not found in LTspice log")
        scale_raw = source.get("scale", 1.0)
        try:
            scale = float(scale_raw)
        except (TypeError, ValueError):
            return VerifyResult(False, "source.scale must be numeric when present")
        if scale != 1.0:
            extracted = f"{float(extracted) * scale:.12g}"
        ok, why = _values_match(value, extracted)
        return VerifyResult(ok, why, extracted)

    measurement = source.get("measurement")
    if not isinstance(measurement, str) or not measurement.strip():
        return VerifyResult(False, "source.measurement missing or not a string")
    step_raw = source.get("step")
    step: int | None
    if step_raw is None:
        step = None
    else:
        try:
            step = int(step_raw)
        except (TypeError, ValueError):
            return VerifyResult(False, "source.step must be an integer when present")
        if step < 1:
            return VerifyResult(False, "source.step must be >= 1")
    field = source.get("field", "value")
    if field != "value":
        return VerifyResult(False, "source.field currently only supports 'value'")
    extracted = _extract_ltspice_measure(text, measurement.strip(), step)
    if extracted is None:
        where = f" step {step}" if step is not None else ""
        return VerifyResult(False, f"measurement {measurement!r}{where} not found in LTspice log")
    ok, why = _values_match(value, extracted)
    return VerifyResult(ok, why, extracted)


def _fetch_sim_run_stdout(run_id: str) -> str:
    """Pull a sim run's stdout via `sim --json logs <id> --field stdout`.

    Retained for archived task contracts whose run-history records reference a
    separate stdout artifact. Current public tasks do not use this path.
    """
    try:
        proc = subprocess.run(
            ["sim", "--json", "logs", str(run_id), "--field", "stdout"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=10, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    try:
        return json.loads(proc.stdout).get("stdout", "") or ""
    except json.JSONDecodeError:
        return ""


def _verify_sim_run_stdout(value, source: dict, sim_records: list[dict]) -> VerifyResult:
    run_id = source.get("run_id")
    extract = source.get("extract", "cat")
    if not isinstance(run_id, str) or not run_id:
        return VerifyResult(False, "source.run_id missing or not a string")
    rec = next((r for r in sim_records if str(r.get("run_id")) == run_id), None)
    if rec is None:
        return VerifyResult(False, f"sim run with run_id {run_id!r} not found in history")
    stdout = rec.get("stdout") or _fetch_sim_run_stdout(run_id)
    if not stdout:
        return VerifyResult(False, f"sim run {run_id} has no captured stdout")
    err = _validate_extractor(extract)
    if err is not None:
        return VerifyResult(False, f"extractor rejected: {err}")
    try:
        proc = subprocess.run(
            ["bash", "-c", extract],
            input=stdout, capture_output=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
    except subprocess.TimeoutExpired:
        return VerifyResult(False, "extractor timed out (>10s)")
    if proc.returncode != 0:
        return VerifyResult(False, f"extractor exited {proc.returncode}: {proc.stderr.strip()[:120]}")
    ok, why = _values_match(value, proc.stdout)
    return VerifyResult(ok, why, proc.stdout.strip()[:200])


def _verify_sim_run_kpi(value, source: dict, sim_records: list[dict]) -> VerifyResult:
    run_id = source.get("run_id")
    field = source.get("field")
    if not isinstance(run_id, str) or not run_id:
        return VerifyResult(False, "source.run_id missing or not a string")
    if not isinstance(field, str) or not field:
        return VerifyResult(False, "source.field missing or not a string")
    rec = next((r for r in sim_records if str(r.get("run_id")) == run_id), None)
    if rec is None:
        return VerifyResult(False, f"sim run with run_id {run_id!r} not found in history")
    parsed = rec.get("parsed_output") or {}
    if field not in parsed:
        return VerifyResult(False, f"field {field!r} not in run {run_id} parsed_output (keys: {list(parsed.keys())[:6]})")
    actual = parsed[field]
    ok, why = _values_match(value, str(actual))
    return VerifyResult(ok, why, str(actual)[:200])


# ──────────────────────────────────────────────────────────────────────
# Top-level dispatch
# ──────────────────────────────────────────────────────────────────────

def verify_source(claim, sim_records: list[dict]) -> VerifyResult:
    """Verify one KPI claim. ``claim`` is the agent-supplied dict.

    ``claim`` must look like ``{"value": <number>, "source": {...}}``.
    A bare number (no source) is rejected.
    """
    if not isinstance(claim, dict):
        return VerifyResult(False, "KPI claim must be an object {value, source}")
    if "value" not in claim:
        return VerifyResult(False, "KPI claim missing 'value'")
    value = claim["value"]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return VerifyResult(False, f"value {value!r} not numeric")
    source = claim.get("source")
    if not isinstance(source, dict):
        return VerifyResult(False, "KPI claim missing 'source' object — bare values are not accepted")
    kind = source.get("kind")
    if kind == "file_extract":
        return _verify_file_extract(value, source)
    if kind == "ltspice_log":
        return _verify_ltspice_log(value, source)
    if kind == "sim_run_stdout":
        return _verify_sim_run_stdout(value, source, sim_records)
    if kind == "sim_run_kpi":
        return _verify_sim_run_kpi(value, source, sim_records)
    return VerifyResult(False, f"unknown source.kind {kind!r}; expected file_extract|ltspice_log|sim_run_stdout|sim_run_kpi")
