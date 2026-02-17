# Flask App Internal Architecture (app.py)

Internal technical reference. Not tracked in git.

---

## Startup & Global State

```python
# Loaded at startup (lines 76-107)
VALID_TICKERS   # set - from valid_tickers.csv
VALID_KEYS      # set - all API keys
TIER_MAP        # dict - key -> tier (public|registered|paid)
PUBLIC_KEY      # str - default key value "public"
RATE_LIMITS     # dict - tier rate limit strings ("5 per 7 days", etc.)
pipeline_lock   # threading.Lock - serializes pipeline execution
```

**Critical:** These are loaded once at startup. Changes to `valid_keys.json` require updating in-memory caches (fixed 2026-01-24, see CHANGES.md).

---

## Routes

| Route | Method | Handler | Auth | Purpose |
|-------|--------|---------|------|---------|
| `/` | GET/POST | `web_ui()` | API key | Web form interface |
| `/run_pipeline` | POST | `run_pipeline()` | API key | JSON API for programmatic access |
| `/trigger_pipeline` | GET | `trigger_pipeline()` | API key | Excel VBA integration (returns file redirect) |
| `/api/financials` | GET | `api_financials()` | API key | JSON financial facts. Supports `source=8k` |
| `/api/filings` | GET | `api_filings()` | API key | SEC filing metadata (includes 8-K) |
| `/api/metric` | GET | `api_metric()` | API key | Specific metric lookup. Supports `source=8k` |
| `/download/<filename>` | GET | `download_file()` | None | Serve generated files (zips .xlsm) |
| `/generate_key` | POST | `generate_key_from_kartra()` | None | Kartra webhook receiver |
| `/admin/usage_summary` | GET | `usage_summary()` | ADMIN_KEY | Usage analytics (includes request_logs) |
| `/admin/check_key_usage` | GET | `check_key_usage()` | ADMIN_KEY | Check rate limit counters |
| `/admin/resolve_key` | GET | `resolve_key()` | ADMIN_KEY | Lookup email by API key |

---

## Authentication Flow

```
Request → get key from ?key= param (default: PUBLIC_KEY)
        → check key in VALID_KEYS set
        → if not found: deny ("Access denied")
        → lookup tier from TIER_MAP (default: "public")
        → apply tier-based rate limits and lock timeouts
```

---

## Rate Limiting

**Backend:** Redis (`redis://localhost:6379/0`)

**Limiter key function:**
- Public tier → IP address (via `get_remote_address()`)
- Registered/Paid → API key

**Limits (per 7 days):**
| Tier | Limit |
|------|-------|
| public | 5 |
| registered | 6 |
| paid | 500 |

**Deduction:** Only on HTTP 200 responses

**429 Response:** API routes (`/api/*`, `/run_pipeline`, `/trigger_pipeline`) return JSON `{status: error, message: ...}`. Web UI (`/`) returns HTML.

---

## Pipeline Lock

Single `threading.Lock()` ensures one pipeline execution at a time.

**Lock acquisition by tier:**
| Tier | Behavior |
|------|----------|
| public | `acquire(blocking=False)` - immediate fail if locked |
| registered | `acquire(timeout=20)` - wait up to 20s |
| paid | `acquire(timeout=60)` - wait up to 60s |

---

## Caching

Before running pipeline, checks:
```python
if os.path.exists(excel_file) and os.path.exists(log_file):
    return cached result
```

Cache cleared on deploy via `update_remote.sh`.

---

## Key Generation (`/generate_key`)

**Trigger:** Kartra webhook POST

**Flow:**
```
1. Parse JSON payload → extract lead.email
2. Check for "Paid" tag → set tier (default: registered)
3. If email exists in valid_keys.json:
   - Update tier
   - Update TIER_MAP in memory
4. If new email:
   - Generate 16-char alphanumeric key
   - Add to valid_keys.json
   - Add to VALID_KEYS and TIER_MAP in memory
5. Send key back to Kartra (background thread, 5s delay)
6. Log to key_issuance_log.jsonl
```

---

## Kartra Integration

**Outbound API call:** `send_key_to_kartra(email, key)`
- Updates lead's `api_key` custom field
- Assigns `Key_created` tag
- Runs in background thread with 5s delay

**Environment variables:**
- `KARTRA_APP_ID`
- `KARTRA_API_KEY`
- `KARTRA_API_PASSWORD`

---

## Logging

| Log | Location | Format | Content |
|-----|----------|--------|---------|
| Request log | `usage_logs/request_log.jsonl` | JSONL | All requests (status, tier, key) |
| Usage log | `usage_logs/usage_log.jsonl` | JSONL | Successful pipeline runs |
| Error log | `error_logs/{ticker}_{q}Q{yr}_error.json` | JSON | Exceptions with traceback |
| Key issuance | `key_issuance_log.jsonl` | JSONL | New key generation audit |
| Pipeline log | `pipeline_logs/{ticker}_{q}Q{yr}_log.txt` | Text | Pipeline output summary |

**Request statuses:** `attempt`, `denied`, `rate_limited`, `locked`, `cache_hit`, `success`, `error`

**Note:** `/admin/usage_summary` returns `request_logs` (raw request log entries) in addition to `data`, `summary`, and `claude_api_logs`.

---

## File Structure (valid_keys.json)

```json
{
  "public": "public",
  "user@email.com": {
    "key": "16charAlphaNum00",
    "tier": "registered"
  }
}
```

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `KARTRA_APP_ID` | Kartra API auth |
| `KARTRA_API_KEY` | Kartra API auth |
| `KARTRA_API_PASSWORD` | Kartra API auth |
| `ADMIN_KEY` | Admin route authentication |

Loaded from `.env` file.

---

## Request Processing (simplified)

```
1. Validate API key (VALID_KEYS check)
2. Lookup tier (TIER_MAP)
3. Check rate limit (Redis)
4. Acquire pipeline lock (tier-based timeout)
5. Validate ticker (VALID_TICKERS check)
6. Check cache (existing export + log)
7. Run edgar_pipeline()
8. Log usage and request
9. Release lock
10. Return result/download link
```

---

## Key Differences Between Routes

| Aspect | `/` (web_ui) | `/run_pipeline` | `/trigger_pipeline` |
|--------|--------------|-----------------|---------------------|
| Method | GET/POST | POST | GET |
| Input | Form data | JSON body | Query params |
| Output | HTML template | JSON response | Redirect to file |
| Rate limit exempt | GET only | No | No |
| Used by | Browser | Programmatic API | Excel VBA |

---

## 8-K Fallback Logic

The `/api/financials` endpoint supports automatic fallback to 8-K extraction when 10-Q/10-K is unavailable:

```
1. Try run_edgar_pipeline() for 10-Q/10-K
2. If `run_edgar_pipeline()` raises `FilingNotFoundError`:
   → Call get_financials_from_8k() from edgar_8k.py
   → Return 8-K data with source="8k" flag
3. Other errors → Return error response (no fallback)
```

**Explicit 8-K request**: Use `source=8k` parameter to skip the 10-Q/10-K attempt.

---

## Known Issues & Fixes

See `CHANGES.md` for documented issues and fixes.
