"""Optional check against the real cmteb.ro page.

Skipped unless RUN_NETWORK_TESTS=1, so the default suite stays offline.
"""

import os

import pytest

from scraper.run import parse_fetched
from scraper.scrape import fetch

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NETWORK_TESTS") != "1",
    reason="network test; set RUN_NETWORK_TESTS=1 to run",
)


def test_live_page_fetches_and_parses():
    """The live page is reachable and parses to a known, non-error status."""
    page = fetch(attempts=1)
    assert page.error is None, page.error
    result = parse_fetched(page)
    assert result.status in ("ok", "empty"), result.error
