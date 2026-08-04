"""Retry schedule — pure computation, no sleeping.

The engine decides *whether* to retry (via core.errors.classify) and does
the sleeping; this module only answers "how long". Full jitter: each delay
is uniform in [0, min(max_delay, base * factor**n)], which avoids thundering
herds when many files fail together.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass

#: Cumulative attempts across runs after which a file is quarantined
#: (5 attempts per run x 3 runs, per the spec).
QUARANTINE_ATTEMPTS = 15


@dataclass(frozen=True, slots=True)
class RetrySchedule:
    max_attempts: int = 5
    base_delay: float = 1.0
    factor: float = 2.0
    max_delay: float = 60.0

    def delays(self, rng: random.Random) -> Iterator[float]:
        """Yield the sleep before each retry: max_attempts - 1 values."""
        for n in range(self.max_attempts - 1):
            ceiling = min(self.max_delay, self.base_delay * self.factor**n)
            yield rng.uniform(0.0, ceiling)
