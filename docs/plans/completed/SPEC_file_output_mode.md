# Spec: File Output Mode for Filing Sections & Earnings Transcripts

## Context

When Claude calls `get_filing_sections` or `get_earnings_transcript` via MCP, the full text response goes directly into the context window. For large sections (MD&A can exceed 10K words, full transcripts 12K+), this burns context fast. Both tools already cache parsed data to disk as JSON, but the MCP response is always inline.

This adds an `output="file"` mode that writes full untruncated content to a readable markdown file on disk and returns just metadata + file path. Claude can then use `Read` with offset/limit or `Grep` for targeted access without the full text ever entering context.

Important: this is still one file per tool call. Filtered calls and unfiltered calls each produce one markdown file.

## Design

### New Parameter

Both tools get: `output: Literal["inline", "file"] = "inline"`

- `"inline"`: current behavior, no change
- `"file"`: writes markdown to disk, returns metadata + file path
- In `"file"` mode:
- `format` and `max_words` are ignored (file always gets full untruncated text)
- Section/speaker/role filters still apply

### Parameter Interactions

| `output` | `format` | `max_words` | Behavior |
|----------|----------|-------------|----------|
| `"inline"` | `"summary"` | (ignored) | Current behavior: metadata only |
| `"inline"` | `"full"` | 3000 | Current behavior: text truncated to 3000 words |
| `"file"` | (ignored) | (ignored) | Writes full untruncated text to file, returns metadata + path |

### Output Directories

- Edgar: `exports/file_output/` (under `edgar_updater`)
- Transcripts: `cache/file_output/` (under `risk_module`)
- Directories must be created on demand (`mkdir(parents=True, exist_ok=True)`)

### File Naming

#### Component sanitization

Every user/content-derived filename component (`ticker`, `symbol`, section keys, role, speaker):
- keep only `[A-Za-z0-9_-]`
- replace all other chars with `_`
- collapse repeated `_`
- trim leading/trailing `_`
- max length 64 chars per component
- fallback to `unknown` if empty after sanitization

#### Collision-proof suffix and basename cap

- Any filtered request (sections/section/role/speaker filter applied) must append a stable 8-char hash suffix.
- Hash input must be canonical JSON of raw filter parameters (sorted keys, normalized value ordering where applicable), then `sha1(...).hexdigest()[:8]`.
- Canonical hash payload must be explicit and tool-specific:
- Edgar payload keys: `tool`, `ticker_raw`, `year`, `quarter`, `sections_raw_sorted`
- Transcript payload keys: `tool`, `symbol_raw`, `year`, `quarter`, `section_raw`, `filter_role_raw`, `filter_speaker_raw`
- `sections_raw_sorted` must be sorted from raw requested section values before sanitization.
- Hash is derived from raw values (before sanitization/truncation) so distinct requests cannot collapse to the same name.
- Enforce `MAX_BASENAME = 180` (excluding `.md`).
- If basename exceeds cap, truncate only the readable prefix and preserve the `_hash8` tail.
- If an unfiltered basename exceeds cap, derive `_hash8` from canonical request params and apply the same truncation rule.
- "Filtered request" definition:
- Edgar is unfiltered when `sections` is omitted OR the requested set equals the full available section-key set for that filing; otherwise filtered.
- Transcript is unfiltered only when `section == "all"` and both `filter_role` and `filter_speaker` are empty.

#### Edgar filenames

- All sections: `{TICKER}_{Q}Q{YY}_sections.md`
- Filtered: `{TICKER}_{Q}Q{YY}_{key1}_{key2}_{hash8}.md`
- Section keys must be sorted alphabetically before join
- Example: `AAPL_4Q24_item_7_b13ac9e2.md`

#### Transcript filenames

- Base: `{SYMBOL}_{Q}Q{YY}_transcript`
- Append `_prepared_remarks` or `_qa` when section is filtered; omit when section is `"all"`
- Append role/speaker suffixes only when used
- Suffix order must be deterministic: `section`, then `role`, then `speaker`
- If any filter supports multiple values, sort values and join with `_and_`
- Any filtered variant appends `_hash8` as final suffix
- Example: `AAPL_4Q24_transcript_qa_cfo_7f29d1aa.md`

### Write Semantics

- Keep deterministic overwrite behavior (same request variant writes same target path)
- All writes must be atomic:
- write to temp file in the same directory
- replace destination with `os.replace(temp_path, target_path)`
- This prevents partial file reads during concurrent calls

### Markdown File Structure

Use grep-friendly canonical headers. Keep body text untruncated.

**Filing sections**:
```md
# AAPL 10-K - Q4 FY2024: Filing Sections
> Sections: item_1a, item_7 | Total words: 18,432
---
## SECTION: Item 7. Management's Discussion and Analysis
**Word count:** 10,217
[full untruncated text...]
### TABLES
[markdown tables...]
```

**Transcripts**:
```md
# AAPL Earnings Call - Q4 FY2024
> Total words: 12,450 | Speakers: 8 | Exchanges: 15
---
## PREPARED REMARKS
### SPEAKER: Tim Cook (CEO)
[full text...]
---
## Q&A SESSION
### EXCHANGE 1: Ben Reitzes (Analyst) -> Tim Cook (CEO)
**Question (Ben Reitzes, Analyst):**
[text...]
**Answer (Tim Cook, CEO):**
[text...]
```

### Response Shape When `output="file"`

Success response (no text content inline):
```python
{
    "status": "success",
    "output": "file",
    "file_path": "/absolute/path/to/file.md",
    "is_empty": False,
    # same metadata as summary mode (word counts, section list, speaker list, etc.)
    "hint": "Use Read tool with file_path. Grep '^## SECTION:' or '^### SPEAKER:' for anchors."
}
```

No-match filters must still return `status="success"` and write a valid markdown file:
- `is_empty: true`
- zeroed counts/word totals
- metadata defaults must be explicit and deterministic:
- common: keep identity fields (`ticker/symbol`, `year`, `quarter`, `output`, `file_path`, `hint`)
- filings: `sections=[]`, `section_count=0`, `total_words=0`
- transcripts: `speakers=[]`, `speaker_count=0`, `exchange_count=0`, `total_words=0`
- file body includes: `No content matched filters.`
- never return an alternate `no_content` status for file mode

Error response on directory/file write failure:
```python
{
    "status": "error",
    "output": "file",
    "error_code": "FILE_WRITE_ERROR",
    "message": "...",
    "file_path": "/absolute/path/attempted.md"
}
```

`file_path` must always be absolute (`Path(...).resolve()`).

## Files to Modify

### Edgar (3 files)

1. **`section_parser.py`**
- Add `output` param to `get_filing_sections_cached()`
- Add `_write_sections_markdown()` helper
- Add shared helpers for filename sanitization and atomic write
- Branch before truncation: if `output="file"`, write markdown and return early with metadata + path

2. **`edgar_tools.py`**
- Add `output` param to `get_filing_sections()`, pass through to `get_filing_sections_cached()`

3. **`mcp_server.py`**
- Add `output` to `get_filing_sections` inputSchema
- Pass `output` in `call_tool()`

### FMP Transcripts (2 files, in `risk_module`)

4. **`mcp_tools/transcripts.py`**
- Add `output` param to `get_earnings_transcript()` and `_apply_filters()`
- Add `_write_transcript_markdown()` helper
- Add shared helpers for filename sanitization and atomic write
- Branch before truncation: if `output="file"`, write markdown and return early with metadata + path

5. **`fmp_mcp_server.py`**
- Add `output` param to `get_earnings_transcript()` wrapper, pass through

### Implementation Order

Edgar (files 1-3) and FMP (files 4-5) are independent and can be done in parallel.
Within each: core logic first, then passthrough, then MCP schema.

## Verification

1. Edgar filing sections:
- Call `get_filing_sections(ticker="AAPL", year=2024, quarter=4, sections=["item_7"], output="file")`
- Verify response has absolute `file_path` and metadata, no text content
- Read markdown with offset/limit and verify full untruncated MD&A
- Grep for `^## SECTION:` to verify section headers are anchorable

2. Earnings transcript:
- Call `get_earnings_transcript(symbol="AAPL", year=2024, quarter=4, section="prepared_remarks", filter_role="CEO", output="file")`
- Verify response has absolute `file_path` and metadata, no text content
- Read markdown and verify full untruncated CEO remarks
- Grep for `^### SPEAKER:` to verify speaker headers are anchorable

3. Backward compatibility:
- Call both tools without `output` and verify identical behavior to current
- Call both tools with explicit `output="inline"` and verify parity with current inline behavior

4. Edge cases:
- No-match filters: verify `status="success"`, `is_empty=true`, zeroed metadata, and markdown contains `No content matched filters.`
- Filename sanitization: weird inputs do not escape directory or create invalid path
- Collision resistance: distinct raw filters that sanitize similarly still produce different `_hash8` names
- Long filenames: verify basename cap enforcement while preserving `_hash8` tail
- File write failure: verify `FILE_WRITE_ERROR` contract
- Concurrent same-params calls: verify no partial output (atomic replace behavior)

---

*Updated: 2026-02-13*
