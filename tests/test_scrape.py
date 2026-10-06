"""Fetching (network faked), raw storage and state handling."""

import urllib.error
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from scraper import scrape

BIG_BODY = b"x" * (scrape.MIN_BODY_BYTES + 1)


def fake_urlopen(status, body):
    """Return a stand-in for urllib.request.urlopen yielding one response.

    Parameters
    ----------
    status : int
        HTTP status to report.
    body : bytes
        Response body.

    Returns
    -------
    unittest.mock.MagicMock
        Callable usable as `urlopen(request, timeout=...)`.
    """
    response = MagicMock()
    response.status = status
    response.read.return_value = body
    opener = MagicMock()
    opener.return_value.__enter__.return_value = response
    return opener


def test_fetch_once_returns_body_and_hash(monkeypatch):
    """A 200 with a large body is accepted and hashed."""
    monkeypatch.setattr(scrape.urllib.request, "urlopen", fake_urlopen(200, BIG_BODY))
    page = scrape.fetch_once("http://example.invalid", timeout=1)
    assert page.body == BIG_BODY
    assert len(page.sha256) == 64
    assert page.error is None


def test_fetch_once_rejects_small_body(monkeypatch):
    """CMTEB error pages are tiny; they must not be stored as the real page."""
    monkeypatch.setattr(scrape.urllib.request, "urlopen", fake_urlopen(200, b"short"))
    with pytest.raises(RuntimeError, match="too small"):
        scrape.fetch_once("http://example.invalid", timeout=1)


def test_fetch_once_rejects_non_200(monkeypatch):
    """A non-200 status is a failure even with a large body."""
    monkeypatch.setattr(scrape.urllib.request, "urlopen", fake_urlopen(204, BIG_BODY))
    with pytest.raises(RuntimeError, match="HTTP 204"):
        scrape.fetch_once("http://example.invalid", timeout=1)


def test_fetch_returns_error_after_all_attempts(monkeypatch):
    """Persistent network failure is returned as Fetched.error, not raised."""
    opener = MagicMock(side_effect=urllib.error.URLError("down"))
    monkeypatch.setattr(scrape.urllib.request, "urlopen", opener)
    page = scrape.fetch("http://example.invalid", attempts=3, timeout=1)
    assert opener.call_count == 3
    assert page.body == b""
    assert page.sha256 == ""
    assert "URLError" in page.error


def test_fetch_retries_then_succeeds(monkeypatch):
    """One failure followed by a good response yields the good response."""
    good = fake_urlopen(200, BIG_BODY)
    opener = MagicMock(side_effect=[OSError("reset"), good.return_value])
    monkeypatch.setattr(scrape.urllib.request, "urlopen", opener)
    page = scrape.fetch("http://example.invalid", attempts=3, timeout=1)
    assert page.error is None
    assert opener.call_count == 2


def test_store_raw_uses_year_month_layout(tmp_path):
    """Raw files go to raw/YYYY/MM/<utc timestamp>.html."""
    page = scrape.Fetched(b"<html/>", "abc", datetime(2026, 9, 18, 18, 6, 12, tzinfo=timezone.utc), 200)
    path = scrape.store_raw(page, tmp_path / "raw")
    assert path == tmp_path / "raw" / "2026" / "09" / "2026-09-18T180612Z.html"
    assert path.read_bytes() == b"<html/>"


def test_state_round_trip(tmp_path):
    """State is empty before the first run and survives a write/read."""
    state_path = tmp_path / "sub" / "state.json"
    assert scrape.read_state(state_path) == {}
    scrape.write_state(state_path, {"last_sha256": "abc"})
    assert scrape.read_state(state_path) == {"last_sha256": "abc"}
    assert not state_path.with_suffix(".json.tmp").exists()
