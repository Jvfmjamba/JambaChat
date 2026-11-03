import time
from collections import deque
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from enum import Enum

from jambachat.config import Settings


class Decision(Enum):
    ALLOW = "allow"
    BLOCK_AND_NOTIFY = "block_and_notify"
    BLOCK = "block"


@dataclass
class _Bucket:
    hits: deque[float] = field(default_factory=deque)
    notified: bool = False


class RateLimiter:
    """Janela deslizante em memória, por usuário. Basta para um único processo."""

    def __init__(
        self,
        limits: list[tuple[int, float]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = sorted(limits, key=lambda limit: limit[1])
        self._window = max(seconds for _, seconds in self._limits)
        self._clock = clock
        self._buckets: dict[Hashable, _Bucket] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> "RateLimiter":
        return cls([(settings.rate_limit_per_minute, 60), (settings.rate_limit_per_day, 86_400)])

    def hit(self, key: Hashable) -> Decision:
        now = self._clock()
        bucket = self._buckets.setdefault(key, _Bucket())
        while bucket.hits and bucket.hits[0] <= now - self._window:
            bucket.hits.popleft()
        for limit, seconds in self._limits:
            recent = sum(1 for t in bucket.hits if t > now - seconds)
            if recent >= limit:
                if bucket.notified:
                    return Decision.BLOCK
                bucket.notified = True
                return Decision.BLOCK_AND_NOTIFY
        bucket.hits.append(now)
        bucket.notified = False
        return Decision.ALLOW
