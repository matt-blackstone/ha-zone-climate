"""Runtime invariant checks on the coordinator's output.

Pure-logic helpers — no Home Assistant imports — so they can be
unit-tested without spinning up HA.

Invariants
----------

* :func:`incompatible_mode_violations` — given a ``GroupDecision`` and
  the group's incompatible-mode pairs, return any pair of zones whose
  *dispatched* modes form an incompatible pair. Should always be empty
  if arbitration is correct; if it ever isn't, the coordinator logs a
  loud ERROR pointing at the offending pair, since that almost
  certainly means a programming bug, a corrupted config, or a future
  refactor that broke the compatibility filter.
"""

from __future__ import annotations

from typing import Iterable

from .models import GroupDecision, HVACMode


def incompatible_mode_violations(
    decision: GroupDecision,
    incompatible_mode_pairs: Iterable[frozenset[HVACMode]],
) -> list[tuple[str, str, HVACMode, HVACMode]]:
    """Return every (zone_a, zone_b, mode_a, mode_b) tuple where two
    zones are concurrently dispatching modes that the group declares
    incompatible.

    OFF is universally compatible — it represents "head not running"
    and is therefore excluded from the scan.

    The returned tuples are normalised so ``zone_a < zone_b``
    lexicographically, making the result stable across coordinator
    refreshes (useful for the loop's "warn-once-per-pair" suppression
    logic).
    """
    incompatible_set: set[frozenset[HVACMode]] = {
        frozenset(p) for p in incompatible_mode_pairs
    }
    if not incompatible_set:
        return []

    live: list[tuple[str, HVACMode]] = sorted(
        (zid, zd.dispatched_mode)
        for zid, zd in decision.zones.items()
        if zd.dispatched_mode is not HVACMode.OFF
    )

    violations: list[tuple[str, str, HVACMode, HVACMode]] = []
    for i, (zone_a, mode_a) in enumerate(live):
        for zone_b, mode_b in live[i + 1 :]:
            if mode_a is mode_b:
                continue
            if frozenset({mode_a, mode_b}) in incompatible_set:
                violations.append((zone_a, zone_b, mode_a, mode_b))
    return violations
