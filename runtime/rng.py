"""SeededRng — the single PRNG. Nothing else may call random / os.urandom.

Wrap Python's random.Random so every nondeterministic choice is seed-derived and reproducible.
"""

from __future__ import annotations

import random
from typing import Sequence, TypeVar

T = TypeVar("T")


class SeededRng:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._r = random.Random(seed)
        self.calls = 0  # for trace debugging

    def _bump(self) -> None:
        self.calls += 1

    # core

    def random(self) -> float:
        self._bump()
        return self._r.random()

    def randint(self, a: int, b: int) -> int:
        self._bump()
        return self._r.randint(a, b)

    def randrange(self, *args: int) -> int:
        self._bump()
        return self._r.randrange(*args)  # type: ignore[arg-type]

    def choice(self, seq: Sequence[T]) -> T:
        self._bump()
        return self._r.choice(seq)  # type: ignore[arg-type]

    def shuffle(self, seq: list[T]) -> None:
        self._bump()
        self._r.shuffle(seq)

    def uniform(self, a: float, b: float) -> float:
        self._bump()
        return self._r.uniform(a, b)

    # helpers used by faults/scheduler

    def coinflip(self, p: float = 0.5) -> bool:
        return self.random() < p

    def jitter(self, base: int, spread: int) -> int:
        """base +/- spread/2, clamped ≥0."""
        return max(0, base + self.randint(-spread // 2, spread // 2))
