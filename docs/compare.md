# Comparing engines

`abmfact compare` runs one ABM-IR model in several engines over the same seeds and asks whether they give the
same results. This is often called docking: aligning two implementations of a model (Axtell et al., 1996). A
translated model is a second implementation of the original, so a comparison keeps each engine's fidelity
report beside its verdicts.

## A comparison file

```yaml
title: "Chemotaxis across engines"
model: ../../examples/ir/chemotaxis.yaml
engines: [reference, physicell, compucell3d, morpheus]
baseline: reference                    # every other engine is compared with this one
seeds: 20
jobs: 10
claims_from: ../../examples/suite/chemotaxis.audit.yaml
metrics:
  - metric: last(mean_x)
    budget: 100                        # micron: a tenth of the domain
    note: "a tenth of the 1000 micron domain"
```

```bash
abmfact compare chemotaxis.compare.yaml --out results/chemotaxis --store runs/
```

| Key | Meaning |
|---|---|
| `model` | the ABM-IR file, relative to the comparison file |
| `engines` | at least two engines; the first is the baseline unless `baseline` names another |
| `metrics` | metric expressions, as in claims, each with a `budget` and an optional `note` |
| `budget` | a number in the metric's units, or `{relative: f}`, a fraction of the baseline's mean |
| `seeds`, `jobs`, `alpha` | seeds per engine, runs at once, and the one-sided significance level (0.05) |
| `seeds_by_engine` | fewer seeds for a slow engine; the reason belongs in a comment or `notes` |
| `seed_start` | the first seed (0); a follow-up starts above the seeds an earlier comparison used, so its sample is fresh |
| `params` | ABM-IR parameters applied in every engine, such as `experiment.max_time` |
| `options` | engine options by engine, as for `run-model --option` |
| `claims_from` | an audit file whose threshold claims are evaluated in every engine |

For a quick look, compare a model directly; every metric then gets the same relative budget:

```bash
abmfact compare examples/ir/immune.yaml --engines reference,morpheus \
    --metric 'last(tumour_live)' --metric 'max(tumour_dead)' --budget 0.25 --seeds 10 --jobs 4
```

## Verdicts

For each metric and engine, ABMfact computes a Welch interval for the difference between the engine's mean and
the baseline's, and reads it against the budget with the same three-outcome test as the audit
([statistics](statistics.md)):

| Verdict | Meaning |
|---|---|
| `AGREE` | the whole interval lies within the budget |
| `DISAGREE` | the whole interval lies outside the budget |
| `INCONCLUSIVE` | neither; the result says how many seeds would settle it |
| `NOT_RUN` | the engine refused the model, is not installed here, or every run failed; the reason is given |

### Following up an INCONCLUSIVE verdict

`seeds_needed` is the number of seeds at which the interval would just clear the budget's edge, if the estimate
did not move. A rerun at that number decides only about half the time. About 3.2 times as many seeds give a
90% chance of a decision when the true difference equals the estimate.

Run the follow-up on fresh seeds, and fix the number before any run. Set `seed_start` above the seeds the first
comparison used. Pooling new seeds with ones already seen makes it a look-then-extend design, which raises the
error rate. An estimate that sits on the budget's edge cannot be settled by any number of seeds; report it as
INCONCLUSIVE. `studies/gate3_followup/` follows up five Gate 3 comparisons this way.

Each threshold claim from `claims_from` is also evaluated in each engine as `holds`, `fails` or `uncertain`, and
`same_conclusion` says whether the engines that decided agree. Two engines can differ numerically and still draw
the same conclusion, or the reverse; both are worth knowing. Monotone claims need a parameter sweep and are left
out.

The command exits with status 1 when any engine disagrees, so it can guard a translation in CI.

## Choosing a budget

A budget says how far apart two engines may be before the difference matters. Write it before any run, as you
would a claim, and take it from the model: a cell diameter for a distance, a fraction of the domain for a
displacement, a rule's half-max for a concentration, a fraction of the population for a count. A budget chosen
after seeing the results can be fitted to them. The Gate 3 study in `studies/gate3/` shows complete comparisons
with budgets and their reasons.

A disagreement is a result. Trace it to its cause: an approximation listed in the fidelity report, a difference
between modelling paradigms (cellular Potts against centre-based cells, say), or a bug. Do not widen the budget
to make it go away.

## Runs and the store

Runs execute in worker processes. With `--store`, finished runs are kept and reused, so an interrupted
comparison resumes where it stopped and a repeated one costs nothing. Only the main process writes the store, and
each engine prepares one run before the workers start, so builds and translation caches are never written twice
at once. `compare.json` holds every engine's runs, means and intervals, the verdicts and the claim statuses.

Axtell, R., Axelrod, R., Epstein, J. M. and Cohen, M. D. (1996). Aligning simulation models: a case study and
results. *Computational and Mathematical Organization Theory*, 1(2), 123-141.
