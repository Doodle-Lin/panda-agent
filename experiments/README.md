# Self-evolution experiments

This directory holds reproducible experiment runs produced by
[`scripts/run_experiment.py`](../scripts/run_experiment.py). Each run emits a
`report.json` (machine-readable) and `report.md` (human-readable) with:

- per-train-task per-round scores, patch accept/reject reasons, and diffs;
- a **held-out generalisation** table — the test split scored before and
  after evolution, so the report answers "did a patch kept on the train
  tasks also help on tasks the loop never saw?"

The directory is gitignored by default: experiments are evidence artefacts,
not source. Commit a run by copying it elsewhere (e.g. `docs/runs/`) when you
want it in the record.

## Running

```bash
# Real LLM run (needs PANDA_API_KEY + config):
python scripts/run_experiment.py --rounds 3 --test-ratio 0.4

# Measure run-to-run variance first and set the gate tolerance from it:
python scripts/run_experiment.py --estimate-noise 3 --rounds 3

# Explicit train/test split:
python scripts/run_experiment.py \
    --train read_and_report search_with_locations count_and_compare \
    --test  apply_edit recover_from_missing_file
```

Output lands in `experiments/<timestamp>/`.

## Why this exists

The repo's core claim is that a patch is kept only when it measurably helps.
That claim is only checkable if someone can run the loop, look at the
numbers, and see what was kept, what was rejected, and whether the kept
patches generalised. This runner is what makes that possible. See
[Roadmap R1](../README.md#roadmap) and the [benchmark walkthrough](benchmark.md).
