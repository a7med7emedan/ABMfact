# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-18

### Added

- Audit files in YAML: model, parameters, claims and budget.
- Claims with threshold, range and monotone tests, and a safe metric grammar.
- Seven checks: determinism, update order, agent order, random streams, grid placement, time step and
  population scale.
- Verdicts ROBUST, SHIFTED, FRAGILE, INCONCLUSIVE, BASELINE_FAILS, NOT_TESTABLE and SKIPPED, with
  reasons, seeds needed and reproducer commands.
- Diagnosis of the likely cause from the pattern of flagged checks.
- Python, Mesa, NetLogo and command adapters.
- Outputs: results.json, a self-contained report.html and an RO-Crate 1.1 record.
- The planted-fault zoo and an acceptance test requiring a diagonal detection matrix.
- Command line: run, check, demo, init and replay.
