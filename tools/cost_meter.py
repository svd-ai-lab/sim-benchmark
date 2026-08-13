"""Per-trial cost meter — quantitative measurements only.

Reads what's already on disk (claude-code.txt + task.toml + reward_detail.json)
and writes a `cost.json` next to each trial. No $ pricing, no hardware-class
look-up — those are platform-specific and belong in a downstream consumer.

What it captures:

  agent  (universal across solver classes — comes from claude-code.txt)
    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
    turns, wall_seconds, model

  compute  (semi-universal — cores/gpus from task.toml, wall from best source)
    cpu_cores, gpu_count           ← task.toml [environment]
    wall_seconds                   ← currently agent.wall_seconds upper bound;
                                     replace with sim-history sum when wired
    wall_seconds_source            ← honesty marker for the above
    cpu_hours = wall × cpu_cores / 3600
    gpu_hours = wall × gpu_count / 3600
    n_runs, n_ok_runs              ← verifier/reward_detail.meta_detail

Usage:
  python tools/cost_meter.py <trial_dir>            # one trial
  python tools/cost_meter.py <job_dir>              # every trial under a job
  python tools/cost_meter.py <path> --print         # to stdout, don't write
  python tools/cost_meter.py <path> --summary       # also print a table
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import tomllib  # Python ≥ 3.11
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


def _last_result_event(claude_code_txt: Path) -> dict | None:
    """Return the last `type=result` JSON line, or None."""
    if not claude_code_txt.exists():
        return None
    text = claude_code_txt.read_text(encoding="utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "result":
            return d
    return None


def _sum_assistant_usage(claude_code_txt: Path) -> dict:
    """Sum per-message usage across every `type=assistant` event in the trial.

    The result event's `usage` field is only the *last* API call's tokens —
    not cumulative — so for direct-Anthropic trials we have to walk the whole
    transcript. ccr-routed trials still get accurate numbers from
    proxy-usage.jsonl in the caller.
    """
    agg = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "n": 0}
    if not claude_code_txt.exists():
        return agg
    for line in claude_code_txt.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") != "assistant":
            continue
        u = ((d.get("message") or {}).get("usage") or {})
        agg["input"]        += int(u.get("input_tokens") or 0)
        agg["output"]       += int(u.get("output_tokens") or 0)
        agg["cache_read"]   += int(u.get("cache_read_input_tokens") or 0)
        agg["cache_create"] += int(u.get("cache_creation_input_tokens") or 0)
        if u:
            agg["n"] += 1
    return agg


def _resources_from_task_toml(case_dir: Path) -> tuple[int | None, int | None, str | None]:
    """Return (cpu_cores, gpu_count, solver) from cases/<.../>task.toml."""
    task = case_dir / "task.toml"
    if not task.exists():
        return (None, None, None)
    try:
        data = tomllib.loads(task.read_text(encoding="utf-8"))
    except Exception:
        return (None, None, None)
    env = data.get("environment") or {}
    sim_md = ((data.get("metadata") or {}).get("sim") or {})
    # Harbor schema is `cpus`; some older cases used `cpu` — accept either.
    cores = env.get("cpus") if env.get("cpus") is not None else env.get("cpu")
    return (cores, env.get("gpus", 0), sim_md.get("solver"))


def _find_case_dir(repo_root: Path, case_id: str) -> Path | None:
    """Search cases/*/<case_id>/task.toml."""
    cases_root = repo_root / "cases"
    if not cases_root.is_dir():
        return None
    for parent in cases_root.iterdir():
        if parent.is_dir():
            candidate = parent / case_id
            if (candidate / "task.toml").exists():
                return candidate
    return None


def _guess_repo_root(start: Path) -> Path | None:
    """Walk up from `start` looking for a sim-benchmark root (has cases/ + tools/)."""
    p = start.resolve()
    for ancestor in (p, *p.parents):
        if (ancestor / "cases").is_dir() and (ancestor / "tools").is_dir():
            return ancestor
    return None


def measure_trial(trial_dir: Path, repo_root: Path | None = None) -> dict:
    """Read trial outputs + matching case spec, return cost dict (no $)."""
    repo_root = repo_root or _guess_repo_root(trial_dir)
    case_id = trial_dir.name.rsplit("__", 1)[0]

    cost: dict = {
        "trial": trial_dir.name,
        "case": case_id,
        "solver_label": None,        # task.toml label ("neutral" if solver-agnostic)
        "solvers_used": None,        # actually invoked by agent (from reward_detail)
        "agent": {
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_tokens": None,
            "cache_creation_tokens": None,
            "reasoning_tokens": None,
            "turns": None,
            "wall_seconds": None,
            "model": None,
            "tokens_source": None,        # claude_code_assistant_sum | openai_usage_proxy
            "proxy_request_count": None,  # how many requests the proxy logged for this trial
            "tokens_unavailable_note": None,
            "total_cost_usd": None,       # trial-cumulative USD (direct-Anthropic only)
        },
        "compute": {
            "cpu_cores": None,
            "gpu_count": None,
            # Two distinct timings — both kept so consumers can pick:
            #   solver_wall_seconds = sum of optional run-history durations
            #   agent_wall_seconds  = total trial wall (mirrors agent.wall_seconds;
            #                          includes LLM thinking + tool latency)
            "solver_wall_seconds": None,
            "agent_wall_seconds": None,
            "wall_seconds_source": None,   # "sim_history" | "agent_total" (fallback)
            "cpu_hours": None,             # derived from solver_wall when available
            "cpu_hours_agent_upper": None, # derived from agent_wall — useful upper bound
            "gpu_hours": None,
            "n_runs": None,
            "n_ok_runs": None,
            "per_run_duration_ms": None,   # {run_id: ms}
        },
    }

    # --- agent: from claude-code.txt's last `type=result` event ---
    cc_path = trial_dir / "agent" / "claude-code.txt"
    res = _last_result_event(cc_path)
    if res:
        # Result event's `usage` is the LAST API call only (not cumulative),
        # so we walk every `type=assistant` event and sum per-message usage.
        # See _sum_assistant_usage() for why.
        sum_u = _sum_assistant_usage(cc_path)
        mu = res.get("modelUsage") or {}
        cost["agent"].update(
            input_tokens=sum_u["input"] or None,
            output_tokens=sum_u["output"] or None,
            cache_read_tokens=sum_u["cache_read"] or None,
            cache_creation_tokens=sum_u["cache_create"] or None,
            turns=res.get("num_turns"),
            wall_seconds=(res.get("duration_ms") or 0) / 1000 or None,
            model=next(iter(mu), None) if mu else None,
        )
        cost["agent"]["tokens_source"] = "claude_code_assistant_sum"
        # Trial-cumulative dollar cost is reported by Claude Code on the
        # result event; surface it for direct-Anthropic runs (ccr-routed
        # paths report 0 here because cost is computed downstream).
        tcu = res.get("total_cost_usd")
        if isinstance(tcu, (int, float)) and tcu > 0:
            cost["agent"]["total_cost_usd"] = tcu

    # --- agent token recovery from openai_usage_proxy sidecar log ---
    # When the agent is routed via ccr → upstream, Claude Code's usage field
    # comes back as zeros (ccr's Anthropic→OpenAI translation strips
    # stream_options.include_usage). The proxy at port 3457 captures the
    # upstream's real `usage` chunk per request and writes one JSONL record
    # per request to /logs/agent/proxy-usage.jsonl. We sum them here.
    proxy_log = trial_dir / "agent" / "proxy-usage.jsonl"
    in_t = cost["agent"]["input_tokens"]
    out_t = cost["agent"]["output_tokens"]
    needs_proxy = (
        proxy_log.exists()
        and (in_t is None or in_t == 0)
        and (out_t is None or out_t == 0)
        and cost["agent"]["turns"]
    )
    if needs_proxy:
        agg = {"input": 0, "output": 0, "cached": 0, "reasoning": 0, "n": 0}
        for line in proxy_log.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            u = rec.get("usage") or {}
            agg["input"]     += int(u.get("prompt_tokens") or 0)
            agg["output"]    += int(u.get("completion_tokens") or 0)
            details = u.get("prompt_tokens_details") or {}
            agg["cached"]    += int(details.get("cached_tokens") or 0)
            ctd = u.get("completion_tokens_details") or {}
            agg["reasoning"] += int(ctd.get("reasoning_tokens") or 0)
            agg["n"]         += 1
        if agg["n"] > 0:
            cost["agent"]["input_tokens"] = agg["input"]
            cost["agent"]["output_tokens"] = agg["output"]
            cost["agent"]["cache_read_tokens"] = agg["cached"]
            cost["agent"]["reasoning_tokens"] = agg["reasoning"]
            cost["agent"]["proxy_request_count"] = agg["n"]
            cost["agent"]["tokens_source"] = "openai_usage_proxy"
    elif res and (in_t is None or in_t == 0) and (out_t is None or out_t == 0) and cost["agent"]["turns"]:
        cost["agent"]["tokens_unavailable_note"] = (
            "upstream returned usage=0 and no proxy-usage.jsonl is present. "
            "If routed via ccr, deploy openai_usage_proxy.py to recover usage data."
        )

    # --- compute resources: from task.toml ---
    if repo_root:
        case_dir = _find_case_dir(repo_root, case_id)
        if case_dir:
            cores, gpus, solver = _resources_from_task_toml(case_dir)
            cost["compute"]["cpu_cores"] = cores
            cost["compute"]["gpu_count"] = gpus
            cost["solver_label"] = solver

    # --- runtime sim runs: from verifier/reward_detail.json ---
    rd = trial_dir / "verifier" / "reward_detail.json"
    total_solver_ms = None
    per_run_ms = None
    if rd.exists():
        try:
            d = json.loads(rd.read_text(encoding="utf-8"))
            md = d.get("meta_detail") or {}
            cost["compute"]["n_runs"] = md.get("n_runs")
            cost["compute"]["n_ok_runs"] = md.get("n_ok_runs")
            cost["solvers_used"] = md.get("solvers")  # list of solvers actually invoked
            total_solver_ms = md.get("total_solver_ms")
            per_run_ms = md.get("per_run_duration_ms")
        except json.JSONDecodeError:
            pass

    # --- two timings: solver-only (true compute) + agent total ---
    if cost["agent"]["wall_seconds"] is not None:
        cost["compute"]["agent_wall_seconds"] = cost["agent"]["wall_seconds"]

    if total_solver_ms is not None:
        cost["compute"]["solver_wall_seconds"] = total_solver_ms / 1000.0
        cost["compute"]["wall_seconds_source"] = "sim_history"
    elif cost["agent"]["wall_seconds"] is not None:
        # Legacy reward_detail (pre-total_solver_ms): no choice but to
        # report agent wall as the canonical compute wall and flag it.
        cost["compute"]["solver_wall_seconds"] = cost["agent"]["wall_seconds"]
        cost["compute"]["wall_seconds_source"] = "agent_total"

    if per_run_ms:
        cost["compute"]["per_run_duration_ms"] = per_run_ms

    # --- derived cpu_hours / gpu_hours ---
    cores = cost["compute"]["cpu_cores"]
    gpus = cost["compute"]["gpu_count"]
    solver_wall = cost["compute"]["solver_wall_seconds"]
    agent_wall = cost["compute"]["agent_wall_seconds"]

    if solver_wall is not None and cores:
        cost["compute"]["cpu_hours"] = round(solver_wall * cores / 3600, 6)
    if agent_wall is not None and cores:
        cost["compute"]["cpu_hours_agent_upper"] = round(agent_wall * cores / 3600, 6)
    if solver_wall is not None and gpus:
        cost["compute"]["gpu_hours"] = round(solver_wall * gpus / 3600, 6)

    return cost


def _is_trial_dir(p: Path) -> bool:
    return (p / "agent" / "claude-code.txt").exists() or (p / "verifier" / "reward.json").exists()


def _fmt_int(n: int | None) -> str:
    return f"{n:>10,}" if isinstance(n, int) else f"{'—':>10}"


def _fmt_float(x: float | None, digits: int = 3) -> str:
    return f"{x:>10.{digits}f}" if isinstance(x, (int, float)) else f"{'—':>10}"


def _print_summary(costs: list[dict]) -> None:
    if not costs:
        return
    rows = []
    for c in costs:
        a, k = c["agent"], c["compute"]
        used = ",".join(c.get("solvers_used") or []) or "?"
        rows.append((
            c["trial"][:38],
            used,
            a["model"] or "?",
            a["input_tokens"], a["output_tokens"], a["cache_read_tokens"],
            a["turns"], a["wall_seconds"],
            k["solver_wall_seconds"], k["cpu_hours"], k["n_runs"], k["n_ok_runs"],
        ))
    print()
    print(f"{'trial':<38} {'used':<10} {'model':<22}"
          f" {'input_tok':>10} {'output_tok':>10} {'cache_read':>10}"
          f" {'turns':>6} {'agent_s':>8} {'solver_s':>9} {'cpu_hr':>8} {'n_runs':>7} {'n_ok':>5}")
    print("-" * 170)
    for r in rows:
        trial, solver, model, intok, outtok, cread, turns, awall, swall, cph, nruns, nok = r
        print(f"{trial:<38} {solver:<10} {model:<22}"
              f" {_fmt_int(intok)} {_fmt_int(outtok)} {_fmt_int(cread)}"
              f" {(str(turns) if turns is not None else '—'):>6}"
              f" {_fmt_float(awall, 1)} {_fmt_float(swall, 1)} {_fmt_float(cph, 4)}"
              f" {(str(nruns) if nruns is not None else '—'):>7}"
              f" {(str(nok) if nok is not None else '—'):>5}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else "")
    ap.add_argument("path", type=Path, help="a trial dir, or a job dir containing trial dirs")
    ap.add_argument("--print", action="store_true", help="print cost JSON to stdout, don't write")
    ap.add_argument("--summary", action="store_true", help="also print a one-row-per-trial summary table")
    ap.add_argument("--repo-root", type=Path, default=None, help="repo root (auto-detected by default)")
    args = ap.parse_args(argv)

    if not args.path.exists():
        print(f"path not found: {args.path}", file=sys.stderr)
        return 1

    repo_root = args.repo_root or _guess_repo_root(args.path)

    trials: list[Path]
    if _is_trial_dir(args.path):
        trials = [args.path]
    else:
        trials = sorted(p for p in args.path.iterdir() if p.is_dir() and _is_trial_dir(p))
        if not trials:
            print(f"no trial dirs under: {args.path}", file=sys.stderr)
            return 1

    costs: list[dict] = []
    for trial in trials:
        c = measure_trial(trial, repo_root)
        costs.append(c)
        if args.print:
            print(json.dumps(c, indent=2))
        else:
            out = trial / "cost.json"
            out.write_text(json.dumps(c, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {out}")

    if args.summary:
        _print_summary(costs)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
