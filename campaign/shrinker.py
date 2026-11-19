"""ddmin shrinker — minimize fault schedule that still fails.

Classic Zeller ddmin: binary-partition delta debugging. Here delta = fault events list.
Predicate: run(seed, faults_subset) still violates same oracle.
Returns minimal subset.
"""

from __future__ import annotations

from typing import Callable


def ddmin_shrink(
    faults: list[str],
    predicate: Callable[[list[str]], bool],
) -> list[str]:
    """Predicate true => still fails. Minimize faults while keeping predicate true."""
    if not faults:
        return []
    if not predicate(faults):
        raise ValueError("predicate must be true for initial set")

    n = 2
    cur = faults[:]
    while len(cur) >= 2:
        chunk_size = max(1, len(cur) // n)
        found = False
        # try chunks and complements
        for i in range(0, len(cur), chunk_size):
            chunk = cur[i : i + chunk_size]
            # try without chunk
            complement = cur[:i] + cur[i + chunk_size :]
            if complement and predicate(complement):
                cur = complement
                n = max(2, n - 1)
                found = True
                break
            # try only chunk (for isolation)
            if predicate(chunk):
                cur = chunk
                n = 2
                found = True
                break
        if not found:
            if n >= len(cur):
                break
            n = min(len(cur), n * 2)
    return cur
