import pytest


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jambachat.llm.http.RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr("jambachat.llm.http.MAX_RETRY_AFTER", 0.0)
