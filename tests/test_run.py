"""End-to-end runs of the command on saved pages, writing to a temp dir."""

import csv
import json

from scraper import run
from scraper.scrape import Fetched
from tests.conftest import CURRENT_PAGE, FIXTURES_DIR


def read_csv_rows(path):
    """Read a CSV into a list of dicts.

    Parameters
    ----------
    path : pathlib.Path
        CSV file.

    Returns
    -------
    list of dict
        One dict per data row.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def only_file(directory, pattern):
    """Return the single file matching a glob pattern under a directory.

    Parameters
    ----------
    directory : pathlib.Path
        Directory to search recursively.
    pattern : str
        Glob pattern.

    Returns
    -------
    pathlib.Path
        The matching file.

    Raises
    ------
    AssertionError
        If there is not exactly one match.
    """
    matches = sorted(directory.rglob(pattern))
    assert len(matches) == 1, matches
    return matches[0]


def test_first_run_writes_raw_observations_snapshot_and_state(tmp_path, capsys):
    """A first run on a page stores everything once."""
    exit_code = run.main(["--input", str(CURRENT_PAGE), "--out", str(tmp_path)])
    assert exit_code == 0
    assert "status=ok changed=True observations=62" in capsys.readouterr().out

    raw_file = only_file(tmp_path / "raw", "*.html")
    assert raw_file.read_bytes() == CURRENT_PAGE.read_bytes()

    observations = read_csv_rows(only_file(tmp_path / "processed" / "observations", "*.csv"))
    assert len(observations) == 62
    assert list(observations[0].keys()) == run.CSV_COLUMNS
    assert observations[0]["pt_name"] == "Ct Luterana"
    assert observations[0]["street"] == "Str Ion Câmpineanu"
    assert observations[0]["buildings"] == "Bl. 3, 6, 4, 5, 6A"

    snapshots = read_csv_rows(only_file(tmp_path / "processed" / "snapshots", "*.csv"))
    assert len(snapshots) == 1
    assert snapshots[0]["status"] == "ok"
    assert snapshots[0]["changed"] == "1"

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["last_status"] == "ok"
    assert state["last_sha256"] == observations[0]["raw_sha256"]


def test_unchanged_page_only_adds_a_snapshot_row(tmp_path, capsys):
    """Running twice on the same page must not duplicate observations or raw files."""
    args = ["--input", str(CURRENT_PAGE), "--out", str(tmp_path)]
    run.main(args)
    run.main(args)
    assert "changed=False" in capsys.readouterr().out
    assert len(list((tmp_path / "raw").rglob("*.html"))) == 1
    observations = read_csv_rows(only_file(tmp_path / "processed" / "observations", "*.csv"))
    assert len(observations) == 62
    snapshots = read_csv_rows(only_file(tmp_path / "processed" / "snapshots", "*.csv"))
    assert [row["changed"] for row in snapshots] == ["1", "0"]
    assert snapshots[1]["raw_path"] == ""


def test_changed_page_appends_new_observations(tmp_path):
    """A different page appends its rows and stores a second raw file."""
    run.main(["--input", str(CURRENT_PAGE), "--out", str(tmp_path)])
    other_page = FIXTURES_DIR / "2021-12-19_first-scrape.html"
    run.main(["--input", str(other_page), "--out", str(tmp_path)])
    observations = read_csv_rows(only_file(tmp_path / "processed" / "observations", "*.csv"))
    assert len(observations) == 62 + 127


def test_empty_page_logs_snapshot_without_observations(tmp_path):
    """An empty page is stored as raw evidence but adds no observation rows."""
    empty_page = FIXTURES_DIR / "2025-01-01_all-sectors-empty.html"
    run.main(["--input", str(empty_page), "--out", str(tmp_path)])
    assert not (tmp_path / "processed" / "observations").exists()
    snapshots = read_csv_rows(only_file(tmp_path / "processed" / "snapshots", "*.csv"))
    assert snapshots[0]["status"] == "empty"


def test_fetch_error_is_logged_and_exit_code_is_zero(tmp_path, monkeypatch, capsys):
    """A dead site still produces a snapshot row so the Action can commit it."""
    failed = Fetched(b"", "", run.fetched_from_bytes(b"").fetched_at_utc, None, error="URLError: down")
    monkeypatch.setattr(run, "fetch", lambda: failed)
    exit_code = run.main(["--out", str(tmp_path)])
    assert exit_code == 0
    assert "status=fetch_error changed=False" in capsys.readouterr().out
    assert not (tmp_path / "raw").exists()
    snapshots = read_csv_rows(only_file(tmp_path / "processed" / "snapshots", "*.csv"))
    assert snapshots[0]["status"] == "fetch_error"
    assert snapshots[0]["error"] == "URLError: down"


def test_dry_run_writes_nothing(tmp_path, capsys):
    """--dry-run prints a preview and leaves the data root untouched."""
    out = tmp_path / "data"
    exit_code = run.main(["--dry-run", "--input", str(CURRENT_PAGE), "--out", str(out)])
    assert exit_code == 0
    printed = capsys.readouterr().out
    assert "22 PTs -> 62 street observations" in printed
    assert "Ct Luterana" in printed
    assert not out.exists()


def test_dry_run_json_prints_every_observation(capsys):
    """--dry-run --json emits valid JSON with one object per observation."""
    run.main(["--dry-run", "--json", "--input", str(CURRENT_PAGE)])
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 62
    assert rows[0]["snapshot_ts_utc"].endswith("Z")


def test_missing_input_exits_with_code_2(tmp_path, capsys):
    """A wrong --input path is a usage error, not a silent success."""
    exit_code = run.main(["--input", str(tmp_path / "nope.html"), "--out", str(tmp_path)])
    assert exit_code == 2
    assert "input not found" in capsys.readouterr().err
