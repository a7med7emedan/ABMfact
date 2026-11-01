# Ensembles

`abmfact ensemble` runs many seeds of one setting in one engine and summarises the result. It answers the first
question about any stochastic model: what does it do on average, and how much does that vary from seed to seed?

```bash
abmfact ensemble examples/ir/monolayer.yaml --engine reference --seeds 20 \
    --param experiment.max_time=1440 --metric 'last(cells)' --jobs 10 --store runs/ --out results/ensemble
```

Every observable is summarised at each save time across the seeds:

| Column | Meaning |
|---|---|
| `mean`, `low`, `high` | the mean and its 90% t-interval, the interval the audit and compare use ([statistics](../statistics.md)) |
| `sd` | the standard deviation between seeds |
| `q05`, `q50`, `q95` | the 5%, 50% and 95% quantiles across seeds |
| `n` | the seeds that reached this time |

Metrics such as `last(cells)` are summarised the same way, and each seed's value is kept beside its seed. The
output folder holds `ensemble.json`, `ensemble.csv` (one row per observable and time) and `report.html`.

| Option | Meaning |
|---|---|
| `--engine` | any engine from `abmfact engines`; native projects work as well as ABM-IR models |
| `--seeds`, `--seed-start` | how many seeds, from which first seed |
| `--param KEY=VALUE` | an ABM-IR parameter for every run, repeatable |
| `--option KEY=VALUE` | an engine option, repeatable |
| `--metric` | a metric to summarise, repeatable |
| `--alpha` | the one-sided level; intervals cover 1 − 2 alpha (90% by default) |
| `--store` | a run store: runs already in it are reused, so a larger ensemble later adds only the new seeds |

The interval is for the mean, not for a single run. To see how far one run can stray, read the quantiles. The
command exits with status 1 when any run failed.
