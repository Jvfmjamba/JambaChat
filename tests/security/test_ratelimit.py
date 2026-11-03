from jambachat.security.ratelimit import Decision, RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_blocks_after_limit_notifies_once_and_recovers() -> None:
    clock = Clock()
    limiter = RateLimiter([(2, 60), (3, 86_400)], clock=clock)

    assert [limiter.hit("a") for _ in range(4)] == [
        Decision.ALLOW,
        Decision.ALLOW,
        Decision.BLOCK_AND_NOTIFY,
        Decision.BLOCK,
    ]
    assert limiter.hit("b") is Decision.ALLOW

    clock.now = 61
    assert limiter.hit("a") is Decision.ALLOW
    assert limiter.hit("a") is Decision.BLOCK_AND_NOTIFY

    clock.now = 86_401
    assert limiter.hit("a") is Decision.ALLOW
