"""A small Mesa 3 SIR model on a torus that honours two ABMfact knobs.

* ``activation`` selects the activation order: ``random`` (Mesa's ``shuffle_do``) or ``id``.
* ``relabel_seed`` creates the agents in a shuffled order, so their ids no longer follow the grid.

Agents decide their next state from a snapshot taken at the start of the step, so a correct model
gives the same distribution of results under either activation order.
"""

from __future__ import annotations

import math

import mesa
import numpy as np

S, I, R = 0, 1, 2


class Person(mesa.Agent):
    def __init__(self, model: SIRModel, cell: int):
        super().__init__(model)
        self.cell = cell
        self.state = S
        self.next_state = S

    def step(self) -> None:
        m = self.model
        if self.state == S:
            k = sum(1 for c in m.neighbours[self.cell] if m.snapshot[c] == I)
            if k and self.random.random() < 1.0 - math.exp(-m.beta * k):
                self.next_state = I
        elif self.state == I and self.random.random() < 1.0 - math.exp(-m.gamma):
            self.next_state = R


class SIRModel(mesa.Model):
    def __init__(
        self,
        beta: float = 0.35,
        gamma: float = 0.1,
        side: int = 15,
        activation: str = "random",
        relabel_seed: int | None = None,
        rng: int | None = None,
    ):
        super().__init__(rng=rng)
        if activation not in ("random", "id"):
            raise ValueError("activation must be 'random' or 'id'")
        self.beta, self.gamma, self.side, self.activation = beta, gamma, side, activation
        n = side * side
        self.neighbours = [
            [((c // side + dy) % side) * side + (c % side + dx) % side for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
            for c in range(n)
        ]
        cells = list(range(n))
        if relabel_seed is not None:
            cells = [int(c) for c in np.random.default_rng(relabel_seed).permutation(n)]
        self.people = {}
        for c in cells:
            self.people[c] = Person(self, c)
        centre = (side // 2) * side + side // 2
        for c in [centre, *self.neighbours[centre]]:
            self.people[c].state = self.people[c].next_state = I
        self.snapshot = [S] * n
        self.ever = {c for c, p in self.people.items() if p.state == I}
        self.datacollector = mesa.DataCollector(model_reporters={"Infected": prevalence})

    def step(self) -> None:
        self.snapshot = [self.people[c].state for c in range(self.side * self.side)]
        if self.activation == "random":
            self.agents.shuffle_do("step")
        else:
            self.agents.sort("unique_id").do("step")
        for p in self.agents:
            p.state = p.next_state
            if p.state == I:
                self.ever.add(p.cell)
        self.datacollector.collect(self)


def prevalence(model: SIRModel) -> float:
    return sum(1 for p in model.agents if p.state == I) / len(model.agents)


def attack_rate(model: SIRModel) -> float:
    return len(model.ever) / len(model.agents)
