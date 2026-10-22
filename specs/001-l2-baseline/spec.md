---
id: SPEC-001
title: L2 baseline for tech-discovery-data
status: in review
created: 2026-10-22
owner: bookchaowalit
---

# SPEC-001: L2 baseline

## Document control

| Field | Value |
|---|---|
| Main-only | yes (ADR-0004 v1.1: red run is local, red and green commits pushed together) |
| Maker | Claude session b-data (2026-10-22) |
| Checker / approver | pending (owner) |

## Problem

The Big Loop scorecard (2026-10-22) had this repo below L2: no lockfile, no type check, no
PRODUCT.md and no error reporting. The API fails closed: every lake error becomes a 503
`unavailable` envelope with no details, so a broken lake is invisible to the operator.

## Behaviour

1. Dependencies resolve from the committed `uv.lock`; CI installs with `uv sync --frozen --all-extras`.
2. `mypy` runs in CI over `src/` and `tests/` (gradual; strict on `discovery_data.observability`).
3. With `SENTRY_DSN` set (and the `sentry` extra), `create_server()` initialises Sentry and a
   lake failure behind a 503 is captured once; the response stays detail-free. Without a DSN,
   Sentry is never initialised.
4. Sentry events carry no PII, frame locals, request bodies or exception text
   (`send_default_pii=False`, `include_local_variables=False`, `max_request_body_size="never"`,
   `before_send=scrub_event`).
5. The boundary matches `discovery-data-contract-v1` offline (identity, port, datasets,
   GET-only consumer routes, normalized and required fields), with known gaps pinned.
6. CI fails when total coverage drops below 90%.

## Acceptance

| AC | Given / When / Then | Test |
|---|---|---|
| AC-01 | Given `SENTRY_DSN`, when the lake read raises, then 503 `unavailable` without details and one exception event (tag `service`) | `tests/test_api_sentry.py::test_lake_failure_reaches_sentry_but_not_the_client` |
| AC-02 | Given `SENTRY_DSN`, when a request succeeds, then nothing is sent | `test_healthy_request_sends_nothing` |
| AC-03 | Given no `SENTRY_DSN`, then Sentry is not initialised | `test_without_dsn_sentry_stays_off` |
| AC-04 | Events drop locals, request data and exception text | `test_init_scrubs_locals_request_bodies_and_messages` |
| AC-05 | The boundary matches the discovery contract offline | `tests/test_contract_discovery_v1.py` |
| AC-06 | CI on `main` is green on Python 3.11 and 3.13 with the coverage gate | CI run below |

## Red/green log

| AC | Red (sha · command · failure) | Green |
|---|---|---|
| AC-01..03 | `43447b9` · `python -m pytest -q tests/test_api_sentry.py` · `AssertionError: 0 != 1` | [run 39755175167](https://github.com/bookchaowalit/tech-discovery-data/actions/runs/39755175167) (success, 3.11 + 3.13) |
| AC-04 | `db9b497` · `python -m pytest -q tests/test_api_sentry.py` · `AssertionError: None is not False` | [run 39755175167](https://github.com/bookchaowalit/tech-discovery-data/actions/runs/39755175167) (success, 3.11 + 3.13) |

## Schedule (planned replacement, D6)

No scheduler was changed (owner decision D6 on the schedule host is open).

- Today: nothing is scheduled. Captures come from book-job-scraping exports; replay into
  Bronze is the manual `scripts/ingest.sh` (data-lake-pipelines `ingest_discovery_captures.py`).
  Discovery is excluded from the Windows scraping plan; the producer's own task state is
  unverified from WSL [ต้องยืนยัน].
- Planned replacement once D6 is decided: one hosted job (owned by data-lake-pipelines'
  portfolio, not this repo) that runs the producer captures, then the discovery replay into
  the chosen lake target, every 24h with a freshness check (technology_signals ≤ 36h,
  history ≤ 192h per roadmap dsc-g4). If the API is hosted, it runs with `SENTRY_DSN` and
  `SENTRY_ENVIRONMENT=production`.

## Out of scope

Live captures, R2/production lake writes, moving capture into this repo, the `source_columns`
producer fix, and a deploy workflow (blocked by D6).
