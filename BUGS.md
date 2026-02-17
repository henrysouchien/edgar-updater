# Edgar Updater — Known Bugs

## Summary (as of 2026-02-17)

- Open bugs in this file: **0**
- Resolved bugs in this file: **2**

## Prioritized Backlog (Unconfirmed Risks / Tech Debt)

These are not confirmed production bugs. They are prioritized follow-ups based on TODOs and behavior risk.

### 1. P1 - Metric period filtering ambiguity (date_type vs full_year_mode)

- **Evidence**: `edgar_tools.py` date-type filtering TODO and fallback behavior.
- **Risk**: `get_metric` may return unexpected period matches (`Q`/`FY`/`YTD`), especially for balance-sheet items.
- **Quick fix**: Define and enforce explicit balance-sheet period handling in `get_metric_from_result()` and date-type normalization rules.
- **Acceptance criteria**:
  - Add tests for `full_year_mode=true/false` across flow and balance-sheet metrics.
  - Returned matches are deterministic and period-appropriate.

### 2. P1 - 8-K discovery window too narrow (`n_limit=8`)

- **Evidence**: `fetch_recent_8k_accessions()` scans only the most recent 8 eligible 8-Ks.
- **Risk**: Older-quarter requests can miss valid 8-K filings outside this window.
- **Quick fix**: Make scan depth configurable and/or extend scanning until requested period is found.
- **Acceptance criteria**:
  - Add test coverage for an older-quarter request beyond the first 8 records.
  - 8-K fallback still resolves the correct filing.

### 3. P2 - `/api/filings` 8-K metadata can be incomplete

- **Evidence**: metadata-only path may omit `exhibit_url`; `period_end` may be inferred.
- **Risk**: Consumers get less reliable filing metadata for audit and traceability.
- **Quick fix**: In metadata-only mode, do lightweight `index.json` resolution to populate `exhibit_url` without full exhibit download.
- **Acceptance criteria**:
  - `/api/filings` includes `exhibit_url` when available.
  - `period_end` source is explicit/consistent.

### 4. P2 - `end_prior` inconsistency in JSON export facts

- **Evidence**: TODO on API column export notes occasional `end_prior` mismatch behavior.
- **Risk**: Downstream clients may rely on incorrect prior-period date endpoints.
- **Quick fix**: Add reconciliation checks during export and fix the mapping path causing stale/mirrored dates.
- **Acceptance criteria**:
  - Add assertions or diagnostics for `end_current`/`end_prior` validity.
  - Known failing case is reproducible and corrected.

### 5. P3 - Missing step-level match diagnostics in pipeline metrics

- **Evidence**: TODO markers for match-step diagnostics logging in quarterly and FY/4Q paths.
- **Risk**: Regressions in match quality are harder to detect and debug quickly.
- **Quick fix**: Log per-step match counts/rates to `metrics/` and expose summary metadata.
- **Acceptance criteria**:
  - Metrics output includes step-level match stats.
  - Final run metadata reports key rates in both workflows.

### 6. P3 - Metric alias coverage gaps

- **Evidence**: alias map intentionally incomplete; TODO to expand as gaps are found.
- **Risk**: Real-world metrics may return false negatives across different filers.
- **Quick fix**: Add alias expansion workflow driven by missed-tag telemetry and targeted tests.
- **Acceptance criteria**:
  - Add coverage for newly discovered high-frequency alias misses.
  - `get_metric` success rate improves on the tracked missed cases.

## 1. `/api/financials` fails with "No valid CIK" for valid tickers

**Status**: Resolved (verified in repo on 2026-02-17)
**Reported**: 2026-02-16
**Severity**: Blocking — prevents API consumers from fetching data for affected tickers

### Problem

`/api/financials?ticker=MSCI&year=2025&quarter=4` returns HTTP 200 with:
```json
{"message": "❌ No valid CIK provided. Please set CIK or lookup from TICKER.", "status": "error"}
```

The ticker passes the `VALID_TICKERS` check (MSCI is in `valid_tickers.csv`), but `lookup_cik_from_ticker()` in `edgar_pipeline.py` fails to resolve it. The 10-K filing exists (filed 2026-02-06, accession `0001408198-26-000011`).

### Reproduction

```bash
# Fails — returns CIK error
curl "https://financialmodelupdater.com/api/financials?ticker=MSCI&year=2025&quarter=4&key=<KEY>"

# Works — same ticker, earlier period
curl "https://financialmodelupdater.com/api/financials?ticker=MSCI&year=2024&quarter=4&key=<KEY>"

# Works — different ticker, same period
curl "https://financialmodelupdater.com/api/financials?ticker=AAPL&year=2024&quarter=4&key=<KEY>"
```

### Root cause (suspected)

`lookup_cik_from_ticker()` in `utils.py` hits `https://www.sec.gov/files/company_tickers.json` on every call. This likely fails intermittently on the server due to:
- SEC rate limiting the server's IP
- Network issues on the deployed server
- No local caching of the ticker→CIK mapping

The same lookup works fine from a local machine.

### Why the web UI still works

The web UI (`/run_pipeline`) checks for a cached Excel file before running the pipeline. If a previous successful run cached the output, it serves from cache without calling `lookup_cik_from_ticker()`. The `/api/financials` endpoint has a separate JSON cache that may not have been populated for this ticker/period combination.

### Fix implemented

1. `utils.py` now uses cache-first ticker lookup with:
   - in-memory TTL cache
   - disk fallback (`company_tickers_cache.json`)
   - retry logic for SEC fetch
2. `/api/financials` now returns typed error `503` with a user-facing CIK-resolution message when SEC lookup fails (instead of passing through the raw pipeline error).
3. Error responses are not cached as successful financial payloads.

### Impact

This blocks the Excel add-in MCP server from running `update_model` for any ticker where the CIK lookup fails on the server. The add-in's backend proxies to `/api/financials`, so the error propagates to the end user as "EDGAR fetch failed (502)".

### Verification

- Test: `tests/test_api_financials.py::test_cik_lookup_failure_returns_503_and_is_not_cached`
- Behavior: CIK failures now return HTTP 503 and do not poison the JSON cache.

## 2. `/api/financials` returns None values for majority of facts

**Status**: Resolved (verified in repo on 2026-02-17)
**Reported**: 2026-02-16
**Severity**: Data gap — most facts have tags but no values, severely limiting update_model matching

### Problem

`/api/financials?ticker=MSCI&year=2025&quarter=4` returns **239 out of 334 facts** with `current_value: None` and `prior_value: None`. This affects facts across all statement types (income statement, balance sheet, cash flow), not just cash flow items.

The same items return correct values when queried individually via `/api/metric` with the same parameters.

### Reproduction

```bash
# Full financials — cash flow facts have None values
curl "https://financialmodelupdater.com/api/financials?ticker=MSCI&year=2025&quarter=4&full_year_mode=true&key=<KEY>"
# Returns: tag=us-gaap:ProceedsFromIssuanceOfDebt | current=None prior=None date_type=FY

# Metric endpoint — same tag returns correct values
curl "https://financialmodelupdater.com/api/metric?ticker=MSCI&year=2025&quarter=4&metric_name=ProceedsFromIssuanceOfDebt&full_year_mode=true&key=<KEY>"
# Returns: current_value=2805963.0, prior_value=556875.0
```

### Impact

The `update_model` tool in the Excel add-in uses `/api/financials` to fetch all facts at once. If cash flow facts come back with None values, those line items won't be matched — leading to fewer cells updated and requiring manual entry or a separate annual CF update pass.

### Fix implemented

1. `app.py` adds `_ensure_metric_value_aliases()` for `/api/financials`.
2. For every fact, endpoint now hydrates:
   - `current_value` from `visual_current_value` fallback `current_period_value`
   - `prior_value` from `visual_prior_value` fallback `prior_period_value`
3. Alias hydration is applied on both fresh responses and cache hits.

### Verification

- Test: `tests/test_api_financials.py::test_financials_response_hydrates_current_and_prior_value_aliases`
- Test: `tests/test_api_financials.py::test_financials_and_metric_agree_on_metric_values`
- Behavior: `/api/financials` and `/api/metric` now expose consistent value fields for matching.
