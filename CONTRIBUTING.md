# Contributing

Thank you for helping. Bug reports with a minimal audit file are the most useful contribution of all.

## Set up

```bash
git clone git@github.com:a7med7emedan/ABMfact.git
cd ABMfact
python -m venv .venv && source .venv/bin/activate
pip install -e ".[mesa]" pytest pytest-cov ruff mypy types-PyYAML
```

## Before opening a pull request

```bash
ruff check . && ruff format --check .
mypy src
pytest -m "not slow"          # fast suite, under a minute
pytest -m slow                # the planted-fault matrix, a few minutes
```

All four must pass. Continuous integration runs the same commands on Python 3.11 to 3.13.

## Adding a check

A new check needs three things, in the same pull request:

1. the check itself in `src/abmfact/checks.py`, and its knob in `src/abmfact/knobs.py`;
2. a fault model in `src/abmfact/zoo.py` with one realistic bug, marked `# FAULT`;
3. the matrix in `tests/test_matrix.py` still diagonal, with the new row and column.

A check without a planted fault that it catches, and that no other check catches, will not be merged.

## Figures

The README figures in `assets/` are generated, never drawn by hand. The detection matrix comes from a real
run of the planted-fault zoo:

```bash
python scripts/make_figures.py      # about a minute with four workers
```

## Commit messages

One short imperative subject line, a blank line, then why the change is needed.

## Conduct

Everyone taking part agrees to the [code of conduct](CODE_OF_CONDUCT.md).
