# PRODUCT — book-discovery-data (`tech-discovery-data`)

Technology-signal data boundary for `discovery.v1`: approved public captures of GitHub
Trending, Hacker News, DEV.to and Product Hunt (produced by book-job-scraping, now the
`career-job-scraper` checkout) are replayed as exact bytes into Bronze
`technology_signals` / `technology_signals_history` and served read-only on
`127.0.0.1:8110`. This repo collects nothing itself.

## Runtime status (verified 2026-10-22)

| What | Status |
|---|---|
| API entry point | `book-discovery-data` / `python -m discovery_data.api` on `127.0.0.1:8110`, GET-only; `POST /v1/refresh` 403 unless `ALLOW_REFRESH` + `REFRESH_TOKEN` (then acknowledge-only, no job). Started by hand |
| Ingest | `scripts/ingest.sh` (manual) runs data-lake-pipelines' `ingest_discovery_captures.py` from `SOLO_DATA_LAKE_ROOT`: capture CSVs → exact bytes in landing → Bronze Parquet → manifest + lineage |
| Who schedules it | Nobody. This WSL host's crontab has no scraper entry (2026-10-22); discovery is excluded from the Windows scraping schedule plan (roadmap dsc-g3). Whether the producer's four capture families run on a Windows task cannot be checked from WSL [ต้องยืนยัน] |
| Hosted deployment | None. No cloud job, no public endpoint |
| Error reporting | Sentry, opt-in: install the `sentry` extra and set `SENTRY_DSN`; a lake failure that the API turns into a detail-free 503 is captured (type + stack only: no locals, request bodies, PII or exception text). No DSN is configured yet [ต้องยืนยัน] |
| Contract | `discovery-data-contract-v1.yaml` (`discovery.v1`, port 8110, additive to the frozen v2 port map); checked offline by `tests/test_contract_discovery_v1.py` |
| Quality gates in CI | ruff, ruff format, mypy (gradual), offline pytest with coverage ≥ 90% on Python 3.11 and 3.13 (lake round-trip included via the `[lake]` extra), shared security scan |

## Users and consumers

Contract-allowed consumers: research, technology-watch, portfolio-planning. None reads the
API live today (loopback only). Consumers use the API or Bronze DuckDB, never scraper CSVs.

## Known gaps

- `source_columns` is listed as a normalized field in the contract, but the producer spreads
  raw CSV columns into the payload instead, so no item carries it (pinned in the contract test).
- The API answers `data_status` `unavailable` (503) and `method_not_allowed` (405), which are
  not in the frozen v2 envelope list.
- No producer schedule decision (roadmap dsc-t3) and no freshness record (dsc-t4).
- Task #91 (historical-origin reconciliation) is still open.
- Deploy workflow: blocked by owner decision D6 (no host for scheduled jobs). See
  [specs/001-l2-baseline/spec.md](specs/001-l2-baseline/spec.md).
