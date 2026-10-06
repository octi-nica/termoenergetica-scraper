# termoenergetica scraper

Scrapes Termoenergetica/CMTEB's district-heating outage table
(<https://www.cmteb.ro/functionare_sistem_termoficare.php>) and stores it as
raw HTML plus monthly CSVs.

The scraper itself is **stdlib only** (Python 3.12+), so the GitHub Action
needs no install step. `pytest` is the only dev dependency.

| path | purpose |
|---|---|
| `scraper/scrape.py` | fetch the page (3 attempts, back-off, size check), raw storage, state file |
| `scraper/parse.py` | outage table -> one row per (table row, punct termic, street) |
| `scraper/run.py` | the command: fetch -> parse -> append CSVs; `--dry-run` writes nothing |
| `scraper/fixtures/` | real page snapshots used by the tests and for offline runs |
| `tests/` | pytest suite (offline by default) |
| `.github/workflows/scrape.yml` | scheduled scrape every 6 h, commits `scraper_data/` |
| `.github/workflows/tests.yml` | runs the test suite on push / PR |

## Output

Under `scraper_data/` (change with `--out`):

```
raw/YYYY/MM/2026-09-18T180612Z.html     page bytes, only when the page changed
processed/observations/YYYY-MM.csv      one row per (table row, PT, street), only when the page changed
processed/snapshots/YYYY-MM.csv         one row per run: status, changed, sha256, counts, error
state.json                              last sha256, last raw file, last status
```

Snapshot `status` is one of `ok`, `empty` (CMTEB's "no records" banner),
`error` (backend error page), `parse_error` (table present, no PT readable),
`fetch_error` (site unreachable). A failed fetch still exits 0 so the Action
commits the log line.

Observation columns: `snapshot_ts_utc`, `snapshot_ts_local` (Europe/Bucharest),
`raw_sha256`, `row_index`, `sector`, `pt_name`, `pt_name_norm`, `blocks_count`,
`blocks_unit`, `street`, `street_recognised`, `buildings`, `zone_raw`,
`agent_raw`, `is_oprire`, `is_deficienta`, `affects_acc`, `affects_inc`,
`cause_raw`, `remediere_raw`, `remediere_iso`.

Each row is one street under one punct termic. `buildings` is the text after
" - " on the street line, as printed (e.g. `bl. A4, B3, D5`). The PT fields
(`pt_name`, `blocks_count`, `zone_raw`, ...) repeat on every street row of the
PT, so sum `blocks_count` over distinct `(raw_sha256, row_index, pt_name)`, not
over rows. A PT with no streets still gets one row with an empty `street`.

## 1. Running locally

From the repository root:

```bash
# Look at the live page; writes nothing
python3 -m scraper.run --dry-run
python3 -m scraper.run --dry-run --limit 25
python3 -m scraper.run --dry-run --json

# Offline, against a saved page
python3 -m scraper.run --dry-run --input scraper/fixtures/functionare_2026-09-18.html

# A real run that writes, kept out of git (scraper_data_local/ is ignored)
python3 -m scraper.run --out scraper_data_local
python3 -m scraper.run --out scraper_data_local   # 2nd run: changed=False, no new observations
```

Use `--out scraper_data_local` for local runs: plain `python3 -m scraper.run`
writes to `scraper_data/`, which is the folder the Action commits.

## 2. Tests

```bash
uv run pytest -q                                   # offline suite
RUN_NETWORK_TESTS=1 uv run pytest -q tests/test_live.py   # also hit the real page
```

| file | covers |
|---|---|
| `tests/test_parse.py` | parser on 5 real page eras + synthetic edge cases (no block count, no `<strong>`, duplicates, table selection, parse_error) |
| `tests/test_scrape.py` | fetch success / small body / non-200 / retries / give-up (network faked), raw layout, state file |
| `tests/test_run.py` | end-to-end runs into a temp dir: first run, unchanged page, changed page, empty page, fetch error, dry run, JSON, missing input |
| `tests/test_live.py` | one real fetch + parse; skipped unless `RUN_NETWORK_TESTS=1` |

## 3. GitHub Actions

`scrape.yml` runs at 02:07, 08:07, 14:07 and 20:07 UTC (and on demand). It
runs `python -m scraper.run` and commits `scraper_data/` as
`github-actions[bot]`. `tests.yml` runs the offline suite on every push and
pull request.

Setup:

1. Create the GitHub repository and push `main`.
2. *Settings -> Actions -> General -> Workflow permissions* -> **Read and write
   permissions**. Without this the commit step fails with `Permission denied`.
3. Replace `<owner>/<repo>` in `USER_AGENT` in `scraper/scrape.py` so the site
   can see who is scraping.
4. *Actions -> Scrape CMTEB outage table -> Run workflow*. Expect a line like
   `status=ok changed=True observations=22 raw=raw/2026/10/...html` and a commit.

Things to know:

- GitHub disables scheduled workflows after 60 days without commits. Every run
  commits at least a snapshot line, so the workflow keeps itself active.
- Scheduled runs often start 5-30 minutes late. The CSVs store the actual fetch time.
- Expect roughly 100-300 MB/year of raw HTML in git history.
- cmteb.ro's `robots.txt` disallows automated access. The scraper identifies
  itself in `User-Agent` and fetches only 4 times a day.
