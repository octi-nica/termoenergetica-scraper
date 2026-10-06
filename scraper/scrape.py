"""Fetch the CMTEB outage page and keep a raw snapshot when it changed.

Stdlib only (no install step in the Action), retry with back-off, refuse tiny
bodies, and store the page only when its SHA-256 differs from the last stored one.

Raw layout (never rewritten):
    scraper_data/raw/YYYY/MM/YYYY-MM-DDTHHMMSSZ.html

State:
    scraper_data/state.json   {"last_sha256": ..., "last_fetch_utc": ..., ...}
"""

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

PAGE_URL = "https://www.cmteb.ro/functionare_sistem_termoficare.php"
# CMTEB's error pages are ~2 KB; the real page is 40-280 KB.
MIN_BODY_BYTES = 10_000
# TODO: replace <owner>/<repo> with the GitHub repository once it exists.
USER_AGENT = "termoenergetica scraper (research; +https://github.com/octi-nica/termoenergetica-scraper)"
ATTEMPTS = 3
TIMEOUT_SECONDS = 30
BACKOFF_SECONDS = 5


@dataclass
class Fetched:
    """The result of one fetch; `error` is set and `body` empty on failure."""

    body: bytes
    sha256: str
    fetched_at_utc: datetime
    http_status: int | None
    error: str | None = None


def fetched_from_bytes(body, http_status=200):
    """Wrap page bytes obtained elsewhere (e.g. a saved file) as a Fetched.

    Parameters
    ----------
    body : bytes
        Page bytes.
    http_status : int or None, optional
        Status to record, by default 200.

    Returns
    -------
    Fetched
        Timestamped now (UTC), with the SHA-256 of `body`.
    """
    sha256 = hashlib.sha256(body).hexdigest()
    return Fetched(body, sha256, datetime.now(timezone.utc), http_status)


def fetch_once(url, timeout):
    """Perform a single GET and validate the response.

    Parameters
    ----------
    url : str
        Page URL.
    timeout : float
        Socket timeout in seconds.

    Returns
    -------
    Fetched
        The successful fetch.

    Raises
    ------
    RuntimeError
        On a non-200 status or a body too small to be the real page.
    urllib.error.URLError, OSError
        On network failure.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = response.status
        body = response.read()
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    if len(body) < MIN_BODY_BYTES:
        raise RuntimeError(f"body too small ({len(body)} bytes), probably an error page")
    return fetched_from_bytes(body, status)


def fetch(url=PAGE_URL, attempts=ATTEMPTS, timeout=TIMEOUT_SECONDS):
    """Fetch the page, retrying with a linear back-off.

    A persistent failure is returned, not raised, so a dead site still
    produces a 'fetch_error' row in the snapshot log.

    Parameters
    ----------
    url : str, optional
        Page URL, by default the CMTEB outage page.
    attempts : int, optional
        Number of tries, by default 3.
    timeout : float, optional
        Socket timeout per try in seconds, by default 30.

    Returns
    -------
    Fetched
        The page, or an empty Fetched with `error` set.
    """
    last_error = None
    for attempt in range(attempts):
        try:
            return fetch_once(url, timeout)
        except (urllib.error.URLError, RuntimeError, TimeoutError, OSError) as err:
            last_error = f"{type(err).__name__}: {err}"
        if attempt < attempts - 1:
            time.sleep(BACKOFF_SECONDS * (attempt + 1))
    return Fetched(b"", "", datetime.now(timezone.utc), None, error=last_error)


def read_state(state_path):
    """Read state.json, or return an empty dict on the first run.

    Parameters
    ----------
    state_path : pathlib.Path
        Path to state.json.

    Returns
    -------
    dict
        Stored state.
    """
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def write_state(state_path, state):
    """Write state.json atomically (temp file then rename).

    Parameters
    ----------
    state_path : pathlib.Path
        Path to state.json.
    state : dict
        State to store.

    Returns
    -------
    None
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(state_path)


def raw_path_for(page, raw_dir):
    """Return where a page's raw HTML belongs: raw/YYYY/MM/<utc-ts>.html.

    Parameters
    ----------
    page : Fetched
        The fetched page.
    raw_dir : pathlib.Path
        Root of the raw archive.

    Returns
    -------
    pathlib.Path
        Target file path.
    """
    ts = page.fetched_at_utc
    return raw_dir / f"{ts:%Y}" / f"{ts:%m}" / f"{ts:%Y-%m-%dT%H%M%SZ}.html"


def store_raw(page, raw_dir):
    """Write the raw page bytes and return the file path.

    Parameters
    ----------
    page : Fetched
        The fetched page.
    raw_dir : pathlib.Path
        Root of the raw archive.

    Returns
    -------
    pathlib.Path
        Path of the written file.
    """
    path = raw_path_for(page, raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(page.body)
    return path
