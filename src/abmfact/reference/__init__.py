"""The built-in reference engine: a small, documented 2D agent-based simulator in NumPy.

It runs ABM-IR models directly: off-lattice centre-based mechanics (overdamped motion with PhysiCell's
repulsion and adhesion potentials), a live cell cycle with division, apoptosis and necrosis, substrates
diffusing on a grid (:mod:`abmfact.reference.diffusion` states the scheme and its stability), secretion
and uptake, chemotaxis, and Hill-type rules combined as PhysiCell combines them. It honours every v0.1
knob, so every audit check applies to it, and the same seed always gives the same run.

It is a baseline for verification and comparison, not a competitor to PhysiCell: no 3D, no
intracellular models, no attack or phagocytosis (planned for milestone 3), and no custom code.
"""

from __future__ import annotations

from .sim import Simulation, refusals, simulate

__all__ = ["Simulation", "refusals", "simulate"]
