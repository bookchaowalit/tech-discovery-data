# book-discovery-data

Lake-first technology discovery data boundary for discovery.v1.

This is the published source boundary for `discovery.v1`. Its canonical origin
is [bookchaowalit/tech-discovery-data](https://github.com/bookchaowalit/tech-discovery-data).

| Publication / release evidence (reviewed 2026-10-15) | State |
| --- | --- |
| Source publication | `external-source-published` |
| Last published source commit recorded by local `origin/main` | `45be1a2215e2ef643b3d55a1165a67d02face842` |
| Last release commit | None recorded; no release tag or approved release evidence in this checkout |
| Deployment | None; no hosted endpoint is claimed |

The source commit is a publication baseline, not proof of a release or owner
acceptance. Historical-origin reconciliation remains a separate owner review
under task #91; it does not mean this published checkout is unavailable.

The approved producer remains book-job-scraping; this product reads its four
public capture families, archives exact bytes through the shared lake adapter,
and exposes a read-only API over committed Bronze Parquet.

    book-job-scraping captures
      -> exact bytes in landing/
      -> Bronze discovery.v1 Parquet
      -> manifest and lineage
      -> read-only API :8110 / DuckDB

## Run locally

Set `SOLO_DATA_LAKE_ROOT` to a data-lake checkout and pass an explicit
lake path:

    SOLO_DATA_LAKE_ROOT=/path/to/solo-data-lake ./scripts/ingest.sh --lake-uri /path/to/lake --json
    ./scripts/test.sh

The test runner uses the data-lake virtualenv on POSIX or Windows/Git Bash.
API contract and capture replay tests run offline with fixtures, without local
exports. Only the optional `book-job-scraping/data/exported` directory check
is skipped with an explicit reason when that directory is absent.

The API reads only the technology_signals and technology_signals_history
Bronze datasets. POST /v1/refresh is disabled by default; collection is an
explicit operator action. When enabled, it only acknowledges (202) a request
whose bearer token matches REFRESH_TOKEN (constant-time comparison); the reply
says `job_started: false` and names the operator command
(`task scraping:discovery:ingest`), because no collection runs from the API.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `API_HOST` | `127.0.0.1` | Bind address (loopback by default) |
| `API_PORT` | `8110` | Port, integer 0-65535 |
| `CORS_ALLOWED_ORIGINS` | empty | Comma-separated extra origins; loopback origins are always allowed |
| `STALE_AFTER_HOURS` | `36` | Records older than this report stale (history uses 4x); finite, > 0 |
| `SOLO_EMPIRE_DATA_LAKE_URI` / `DATA_LAKE_URI` | empty | Lake root to read Bronze from |
| `SOLO_DATA_LAKE_ROOT` | empty | data-lake checkout used to locate the shared runtime when the `[lake]` extra is not installed |
| `LAKE_READ_MODE` | `parquet` | Passed to the shared lake reader |
| `LAKE_READ_FALLBACK` | `error` | Passed to the shared lake reader |
| `ALLOW_REFRESH` / `REFRESH_TOKEN` | off / empty | Enable the acknowledge-only `POST /v1/refresh` |
| `FREE_ONLY`, `ALLOW_PAID_PROVIDERS`, `ALLOW_EXTERNAL_WRITES` | on / off / off | Policy flags reported by `/v1/metadata` |

Invalid `API_PORT` or `STALE_AFTER_HOURS` values stop start-up with a
`ConfigError` naming the variable (blank values use the default).

The shared `data_lake` runtime comes from the `[lake]` extra
(`solo-empire-data-lake`, pinned to a commit) or from the checkout named by
`SOLO_DATA_LAKE_ROOT`. If it is missing or the lake read
fails, GET endpoints answer `503` with `data_status: "unavailable"` and no
paths or exception text; 404/403/400 responses never need the runtime.

## Standalone checks and CI

Without a Solo Empire checkout (as in `.github/workflows/ci.yml`):

    uv sync --frozen --all-extras        # uv.lock: package + [dev,lake,sentry]
    uv run ruff check . && uv run ruff format --check .
    uv run mypy                           # scope: [tool.mypy] files in pyproject.toml
    uv run python -m pytest -q -rs --cov  # CI gate: total coverage >= 90%

Error reporting is opt-in: with the `sentry` extra and `SENTRY_DSN` set, a lake failure
behind a 503 is sent to Sentry (type and stack only). See [PRODUCT.md](PRODUCT.md).

`tests/test_http_api.py` drives the real HTTP handler on a loopback port with
in-memory store stubs (pagination, single record, CORS, refresh auth, 503).
`tests/test_lake_roundtrip.py` lands Bronze rows into a temporary lake and
reads them back through `load_records`/`get_record`/`load_history`. It and the
item-builder test skip with an explicit reason when the `[lake]` runtime is
absent (install only `.[dev]` for a dependency-free run); `./scripts/test.sh` still runs the full suite plus the
capture-replay test from the data-lake checkout (`SOLO_DATA_LAKE_ROOT`).

## Contract

The boundary implements `discovery.v1` from the portfolio's
`systems/architecture/discovery-data-contract-v1.yaml`:

- source book-discovery-data, domain discovery, schema discovery.v1;
- approved public captures from GitHub, Hacker News, DEV.to, and Product Hunt;
- attribution and source URLs are retained on every normalized signal;
- malformed or ambiguous capture rows fail closed before any projection;
- consumers use the API or Bronze DuckDB and never read scraper CSVs directly.

This product has no deployment. Task #91 is limited to historical-origin
reconciliation and normal review/release evidence; its open status does not
contradict the published source boundary above.
