"""Shared helpers: paths to the real page snapshots in scraper/fixtures/."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "scraper" / "fixtures"
CURRENT_PAGE = FIXTURES_DIR / "functionare_2026-09-18.html"


def load_fixture(name):
    """Return the text of a fixture page.

    Parameters
    ----------
    name : str
        File name inside scraper/fixtures/.

    Returns
    -------
    str
        Page source, decoded as the scraper decodes it.
    """
    return (FIXTURES_DIR / name).read_text(encoding="utf-8", errors="replace")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Make retry back-off instant so failing-fetch tests run fast."""
    monkeypatch.setattr("scraper.scrape.time.sleep", lambda seconds: None)
