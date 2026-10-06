"""Scrape + parse + store. Stdlib only. Run from the repository root:

    python3 -m scraper.run                       # fetch, store raw if changed, append CSV rows
    python3 -m scraper.run --dry-run             # fetch live page, parse, print; write NOTHING
    python3 -m scraper.run --dry-run --input scraper/fixtures/functionare_2026-09-18.html
    python3 -m scraper.run --input FILE          # treat a saved page as fetched now (writes)
    python3 -m scraper.run --out some/dir        # data root (default: scraper_data/)

Outputs under the data root:
    raw/YYYY/MM/<ts>.html                  the page bytes, only when the SHA-256 changed
    processed/observations/YYYY-MM.csv     one row per (table row, punct termic, street) per changed page
    processed/snapshots/YYYY-MM.csv        one row per run: status, sha256, changed, counts
    state.json                             last sha256 / paths / status

Exit code is 0 even when the fetch fails: the failure is recorded in the
snapshot log and the Action still commits it (losing the log entry would be
worse). Exit code 2 only when --input points to a missing file.
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from scraper.parse import CSV_COLUMNS, ParseResult, parse_page
from scraper.scrape import fetch, fetched_from_bytes, read_state, store_raw, write_state

TZ = ZoneInfo("Europe/Bucharest")
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "scraper_data"
SNAPSHOT_COLUMNS = [
    "snapshot_ts_utc", "status", "changed", "raw_sha256", "bytes", "n_rows",
    "n_observations", "raw_path", "error",
]
UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def append_csv(path, columns, rows):
    """Append rows to a CSV, writing the header if the file is new.

    Parameters
    ----------
    path : pathlib.Path
        CSV file.
    columns : list of str
        Column order; keys not in it are ignored.
    rows : list of dict
        Rows to append. Nothing is written (or created) if empty.

    Returns
    -------
    None
    """
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def page_status(page, result):
    """Return the snapshot status: 'fetch_error' or the parser's status.

    Parameters
    ----------
    page : scraper.scrape.Fetched
        The fetched page.
    result : ParseResult
        Parse outcome.

    Returns
    -------
    str
        Status string.
    """
    if page.error:
        return "fetch_error"
    return result.status


def observation_rows(result, page):
    """Build the observation CSV rows for one page.

    Parameters
    ----------
    result : ParseResult
        Parse outcome.
    page : scraper.scrape.Fetched
        The fetched page (timestamp and SHA-256 are copied onto each row).

    Returns
    -------
    list of dict
        One row per observation, columns as in CSV_COLUMNS.
    """
    ts_local = page.fetched_at_utc.astimezone(TZ)
    rows = []
    for observation in result.observations:
        row = {
            "snapshot_ts_utc": page.fetched_at_utc.strftime(UTC_FORMAT),
            "snapshot_ts_local": ts_local.strftime("%Y-%m-%dT%H:%M:%S"),
            "raw_sha256": page.sha256,
        }
        row.update(observation.as_row())
        rows.append(row)
    return rows


def snapshot_row(result, page, changed, raw_path):
    """Build the snapshot-log row for one run.

    Parameters
    ----------
    result : ParseResult
        Parse outcome.
    page : scraper.scrape.Fetched
        The fetched page.
    changed : bool
        Whether the page differs from the last stored one.
    raw_path : pathlib.Path or None
        Raw file path relative to the data root, if one was written.

    Returns
    -------
    dict
        Row with the columns in SNAPSHOT_COLUMNS.
    """
    error = page.error or result.error or ""
    raw_path_text = ""
    if raw_path is not None:
        raw_path_text = str(raw_path)
    return {
        "snapshot_ts_utc": page.fetched_at_utc.strftime(UTC_FORMAT),
        "status": page_status(page, result),
        "changed": int(changed),
        "raw_sha256": page.sha256,
        "bytes": len(page.body),
        "n_rows": result.n_rows,
        "n_observations": len(result.observations),
        "raw_path": raw_path_text,
        "error": error,
    }


def parse_fetched(page):
    """Parse a fetched page, or return an 'error' result if the fetch failed.

    Parameters
    ----------
    page : scraper.scrape.Fetched
        The fetched page.

    Returns
    -------
    ParseResult
        Parse outcome.
    """
    if page.error:
        return ParseResult("error", [], 0, page.error)
    html = page.body.decode("utf-8", errors="replace")
    return parse_page(html)


def flags_label(observation):
    """Return e.g. 'OPRIRE/ACC' for an observation's set flags.

    Parameters
    ----------
    observation : scraper.parse.Observation
        One observation.

    Returns
    -------
    str
        Set flags joined by '/'.
    """
    labels = []
    if observation.is_oprire:
        labels.append("OPRIRE")
    if observation.is_deficienta:
        labels.append("DEFICIENTA")
    if observation.affects_acc:
        labels.append("ACC")
    if observation.affects_inc:
        labels.append("INC")
    return "/".join(labels)


def distinct_pts(observations):
    """Return one observation per (table row, PT), in order.

    Observations are per street, so the PT-level fields (e.g. blocks_count)
    repeat; totals over PTs must use this list to avoid double counting.

    Parameters
    ----------
    observations : list of scraper.parse.Observation
        Parsed observations.

    Returns
    -------
    list of scraper.parse.Observation
        The first observation of each (row_index, pt_name, zone_raw).
    """
    seen = set()
    firsts = []
    for observation in observations:
        # zone_raw tells apart two PTs with the same name in one table row.
        key = (observation.row_index, observation.pt_name, observation.zone_raw)
        if key in seen:
            continue
        seen.add(key)
        firsts.append(observation)
    return firsts


def print_summary(result, page):
    """Print the page-level summary of a dry run.

    Parameters
    ----------
    result : ParseResult
        Parse outcome.
    page : scraper.scrape.Fetched
        The fetched page.

    Returns
    -------
    None
    """
    fetched = page.fetched_at_utc.isoformat(timespec="seconds")
    print(f"fetched  : {fetched}  {len(page.body):,} bytes  sha256 {page.sha256[:12]}")
    status_line = f"status   : {page_status(page, result)}"
    if result.error:
        status_line += f"  ({result.error})"
    print(status_line)
    n_pts = len(distinct_pts(result.observations))
    print(f"rows     : {result.n_rows} table rows -> {n_pts} PTs "
          f"-> {len(result.observations)} street observations")


def print_totals(observations):
    """Print sector, agent, block and street totals over all observations.

    Parameters
    ----------
    observations : list of scraper.parse.Observation
        Parsed observations.

    Returns
    -------
    None
    """
    sectors = set()
    agents = set()
    unrecognised = 0
    for observation in observations:
        sectors.add(observation.sector)
        agents.add(observation.agent_raw)
        if observation.street and not observation.street_recognised:
            unrecognised += 1
    total_blocks = 0
    missing_counts = 0
    for observation in distinct_pts(observations):
        if observation.blocks_count is None:
            missing_counts += 1
        else:
            total_blocks += observation.blocks_count
    print(f"sectors  : {sorted(sectors, key=str)}")
    print(f"agents   : {sorted(agents)}")
    print(f"blocks   : {total_blocks} total; {missing_counts} PT(s) without a count")
    print(f"streets  : {len(observations)} rows; unrecognised prefixes: {unrecognised}")


def print_observation(observation):
    """Print one observation as a short block of lines.

    Parameters
    ----------
    observation : scraper.parse.Observation
        Observation to print.

    Returns
    -------
    None
    """
    until = observation.remediere_iso or repr(observation.remediere_raw)
    print(
        f"  S{observation.sector} | {observation.pt_name!r:<32} "
        f"blocks={str(observation.blocks_count):<4} "
        f"{flags_label(observation):<22} until {until}"
    )
    street_note = ""
    if observation.street and not observation.street_recognised:
        street_note = "  (unrecognised prefix)"
    print(f"       street: {observation.street}{street_note}")
    print(f"       blds  : {observation.buildings[:140]}")
    print(f"       cause : {observation.cause_raw[:90]}")


def print_preview(result, page, limit):
    """Print a human-readable dry-run report.

    Parameters
    ----------
    result : ParseResult
        Parse outcome.
    page : scraper.scrape.Fetched
        The fetched page.
    limit : int
        Maximum number of observations to print in detail.

    Returns
    -------
    None
    """
    print_summary(result, page)
    if not result.observations:
        return
    print_totals(result.observations)
    shown = result.observations[:limit]
    print(f"\nfirst {len(shown)} observations:")
    for observation in shown:
        print_observation(observation)


def store_snapshot(result, page, out):
    """Write raw HTML, CSV rows and state for one run.

    Raw HTML and observation rows are written only when the page changed:
    an unchanged page is not a new observation. The snapshot log gets a row on
    every run, so "site down" can be told apart from "nothing new".

    Parameters
    ----------
    result : ParseResult
        Parse outcome.
    page : scraper.scrape.Fetched
        The fetched page.
    out : pathlib.Path
        Data root.

    Returns
    -------
    dict
        The updated state, plus "changed" (bool) and "raw_path" (relative
        path or None) for reporting.
    """
    state_path = out / "state.json"
    state = read_state(state_path)
    month = page.fetched_at_utc.strftime("%Y-%m")
    changed = bool(page.sha256) and page.sha256 != state.get("last_sha256")
    raw_path = None
    if changed:
        raw_path = store_raw(page, out / "raw").relative_to(out)
        append_csv(out / "processed" / "observations" / f"{month}.csv",
                   CSV_COLUMNS, observation_rows(result, page))
        state["last_sha256"] = page.sha256
        state["last_raw_path"] = str(raw_path)
        state["last_change_utc"] = page.fetched_at_utc.isoformat(timespec="seconds")
    state["last_fetch_utc"] = page.fetched_at_utc.isoformat(timespec="seconds")
    state["last_status"] = page_status(page, result)
    append_csv(out / "processed" / "snapshots" / f"{month}.csv",
               SNAPSHOT_COLUMNS, [snapshot_row(result, page, changed, raw_path)])
    write_state(state_path, state)
    report = dict(state)
    report["changed"] = changed
    report["raw_path"] = raw_path
    return report


def build_arg_parser():
    """Build the command-line parser.

    Returns
    -------
    argparse.ArgumentParser
        Parser for the options described in the module docstring.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="parse and print; write nothing")
    parser.add_argument("--input", help="parse this saved HTML file instead of fetching")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="data root (default scraper_data/)")
    parser.add_argument("--limit", type=int, default=8,
                        help="observations to print in --dry-run")
    parser.add_argument("--json", action="store_true",
                        help="in --dry-run, print all observations as JSON instead")
    return parser


def run_dry(result, page, args):
    """Print the dry-run output (preview or JSON); write nothing.

    Parameters
    ----------
    result : ParseResult
        Parse outcome.
    page : scraper.scrape.Fetched
        The fetched page.
    args : argparse.Namespace
        Parsed options (uses `json` and `limit`).

    Returns
    -------
    None
    """
    if args.json:
        rows = observation_rows(result, page)
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print_preview(result, page, args.limit)


def main(argv=None):
    """Run the scraper from the command line.

    Parameters
    ----------
    argv : list of str or None, optional
        Arguments; None means sys.argv[1:].

    Returns
    -------
    int
        Exit code: 0 normally (including fetch failures), 2 if --input is missing.
    """
    args = build_arg_parser().parse_args(argv)
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"input not found: {input_path}", file=sys.stderr)
            return 2
        page = fetched_from_bytes(input_path.read_bytes())
    else:
        page = fetch()

    result = parse_fetched(page)
    if args.dry_run:
        run_dry(result, page, args)
        return 0

    report = store_snapshot(result, page, args.out)
    raw_text = "-"
    if report["raw_path"] is not None:
        raw_text = str(report["raw_path"])
    print(f"status={report['last_status']} changed={report['changed']} "
          f"observations={len(result.observations)} raw={raw_text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
