# Leaderboard methodology

The published results are at
[hwe-bench.svdailab.com](https://hwe-bench.svdailab.com/).

## Unit of comparison

A leaderboard row must identify:

- dataset or frozen task-set version;
- repository commit and evaluator/image digest;
- agent and model;
- inference settings;
- resource and time budgets; and
- number of trials per task.

Changing any of these creates a different measurement population.

## Task scores

Each task writes one scalar Harbor reward in `reward.json` and a diagnostic
breakdown in `reward_detail.json`. The score contract is defined in
[`SCHEMA.md`](SCHEMA.md).

An assigned task that produces no valid verifier result counts as zero unless a
documented infrastructure failure invalidates the trial and it is rerun. Do not
silently average only completed tasks.

## Aggregation

Report per-track means for combustion, battery and CFD. If a single headline
score is reported, use the equal-weighted mean of the three track means so that
adding more parameter variants to one track does not change its weight.

```text
headline = mean(combustion_mean, battery_mean, cfd_mean)
```

Every compared model must be evaluated on the same frozen task set. Report task
coverage and the count of failed or missing trials beside the score.

## Uncertainty

When tasks have repeated trials, compute uncertainty from paired per-task
differences because the same tasks are run for each model. State the number of
repeats and the interval method. A point estimate without its task set and
coverage is not a reproducible leaderboard claim.

## Interpretation

- A score measures performance on this task set and environment, not hardware
  engineering in general.
- A solver-produced reference establishes a tool-relative target; it is not an
  experimental measurement unless the task provenance says so.
- Diagnostic failures distinguish task execution, reproduction, physics and KPI
  accuracy, but only the scalar reward determines the leaderboard score.
- Costs and wall time should be reported separately from accuracy.

## Local aggregation

For a Harbor job directory:

```bash
python tools/aggregate_leaderboard.py jobs/<job-directory>
python tools/aggregate_economics.py jobs/<job-directory>
```

These utilities summarize available artifacts; reviewers must still verify that
the included trials share the comparison coordinates listed above.
