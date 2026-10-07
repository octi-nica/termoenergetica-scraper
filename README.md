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


