"""FIRMS source: waiting out the MAP_KEY transaction limit instead of failing."""

from datetime import date

import pytest

from include.firms import source


class FakeResponse:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise source.requests.HTTPError(response=self)


def test_waits_out_the_transaction_limit_then_succeeds(monkeypatch):
    answers = iter([FakeResponse(400, "Exceeding allowed transaction limit."),
                    FakeResponse(400, "Exceeding allowed transaction limit."),
                    FakeResponse(200, "latitude,longitude,frp\n-41.5,-71.8,12.3\n")])
    monkeypatch.setattr(source.requests, "get", lambda url, timeout: next(answers))
    waits = []
    monkeypatch.setattr("time.sleep", waits.append)
    rows = source.fetch_firms_csv("KEY", "MODIS_SP", "world", 5, date(2025, 1, 1))
    assert rows == [{"latitude": -41.5, "longitude": -71.8, "frp": 12.3}]
    assert waits == [source.TRANSACTION_LIMIT_WAIT_S] * 2


def test_other_errors_fail_at_once_without_the_key(monkeypatch):
    monkeypatch.setattr(source.requests, "get", lambda url, timeout: FakeResponse(400, "Invalid MAP_KEY."))
    monkeypatch.setattr("time.sleep", lambda s: pytest.fail("should not wait"))
    with pytest.raises(RuntimeError, match="Invalid MAP_KEY") as e:
        source.fetch_firms_csv("SECRETKEY", "MODIS_SP", "world", 5, date(2025, 1, 1))
    assert "SECRETKEY" not in str(e.value)
