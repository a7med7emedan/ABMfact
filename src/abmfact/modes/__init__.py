"""Modes beyond a single run: ensembles of seeds, parameter sweeps and sensitivity analysis.

Each mode runs one model through the engine contract, many times, and reuses the run store so a repeated or
interrupted mode picks up where it stopped. Submodules are imported on use, so ``import abmfact`` stays light.
"""
