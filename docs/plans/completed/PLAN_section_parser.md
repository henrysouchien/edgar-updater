# PLAN: 10-K/10-Q Section Parser

## Goal

Add a `section_parser.py` module that parses SEC filing HTML into named qualitative sections (Business, Risk Factors, MD&A, etc.) and expose it as an MCP tool and optional Flask endpoint.

**Spec:** `/Users/henrychien/Documents/Jupyter/edgar_updater/docs/SPEC_section_parser.md`

**Protocol:** `/Users/henrychien/Documents/Jupyter/docs/PROTOCOL_agent_tool_responses.md` -- governs default `format="summary"`, `max_words` truncation, and unfiltered-full-request behavior.

---

## Architecture Overview

```
MCP / Flask
    |
    v
edgar_tools.get_filing_sections()      <-- NEW wrapper in edgar_tools.py
    |
    v
section_parser.get_filing_sections_cached()   <-- NEW file
    |
    +-- section_parser.fetch_filing_html()     <-- thin wrapper
    |       |
    |       +-- edgar_tools.get_filings()      <-- EXISTING
    |       +-- edgar_tools.fetch_filing_htm()  <-- NEW shared helper
    |
    +-- section_parser.parse_filing_sections()
            |
            +-- find_section_headers()
            +-- extract_section_content()
            +-- html_to_text()
            +-- table_to_markdown()
```

---

## Implementation Order (5 phases)

### Phase 1: Add `fetch_filing_htm()` to `edgar_tools.py`
### Phase 2: Create `section_parser.py` (core parsing)
### Phase 3: Add `get_filing_sections()` wrapper to `edgar_tools.py`
### Phase 4: Register MCP tool in `mcp_server.py`
### Phase 5: Add Flask route to `app.py` (optional)

Each phase is independently testable. Do NOT proceed to the next phase until the current one passes its tests.

---

## Phase 1: Add `fetch_filing_htm()` to `edgar_tools.py`

### What

Add a new function `fetch_filing_htm(cik, accession)` to `/Users/henrychien/Documents/Jupyter/edgar_updater/edgar_tools.py`. This extracts the "fetch index.json, pick largest .htm, download it" logic that currently lives inside `edgar_pipeline.py`'s nested `try_all_htm_files()` function (lines 1270-1296).

### Why duplicate instead of refactor

The prior MCP refactoring plan (see `/Users/henrychien/Documents/Jupyter/edgar_updater/docs/plans/completed/PLAN-edgar-mcp-refactor.md`, line 593) explicitly chose to **duplicate** logic in `edgar_tools.py` rather than modify the monolithic `edgar_pipeline.py`, to reduce production risk. The functions inside `edgar_pipeline.py` are nested inside `run_edgar_pipeline()` (a ~3500-line function), and they depend on inner scope variables like `STOP_AFTER_FIRST_VALID_PERIOD`, `extract_facts_with_document_period()`, and `get_concept_roles_from_presentation()`. Extracting them would require significant untangling. Follow the same safe approach: duplicate only the document-fetching logic in `edgar_tools.py`, leave `edgar_pipeline.py` untouched.

### Function Signature

```python
def fetch_filing_htm(cik: str, accession: str) -> tuple[bytes, str]:
    """
    Fetch the main .htm file from an SEC filing accession.

    Uses the same strategy as try_all_htm_files(): fetch index.json,
    sort .htm files by size descending, download the largest one.
    Falls back to next-largest if download fails.

    Args:
        cik: SEC Central Index Key (e.g., "0000320193"). Will be
             cast to int to strip leading zeros for URL construction.
        accession: SEC accession number (e.g., "0000320193-23-000055").

    Returns:
        (html_bytes, htm_url) -- raw HTML content and the URL it came from.

    Raises:
        ValueError: if no valid .htm file found in the accession.
    """
```

### Implementation Details

Add these imports at the top of `edgar_tools.py` (some already exist):

```python
import time
from config import HEADERS, REQUEST_DELAY, N_10K, N_10Q  # REQUEST_DELAY is new import
```

The function body (see spec lines 63-85 for exact code):

1. Build `acc_nodash` by stripping dashes from the accession number.
2. Build `index_url` = `https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/index.json`
3. Build `base_url` = `https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/`
4. `GET index_url` with `headers=HEADERS`, sleep `REQUEST_DELAY`, raise on error.
5. Parse JSON, get `directory.item` list.
6. Separate `.htm` files into two lists: `sized` (those with valid digit `size` metadata, sorted descending by `int(size)`) and `unsized` (the rest, in original index order).
7. Build candidate list: `sized + unsized`. This tries the largest known files first, then falls back to unsized files. Matches the existing pipeline behavior (`edgar_pipeline.py:1325-1330`) which scans all `.htm` entries.
8. Loop through the candidate list: `GET base_url + name`, sleep, check `resp.ok and len(resp.content) > 10_000` (skip tiny exhibits).
9. Return `(resp.content, url)` on first success.
10. Raise `ValueError` if loop exhausts without success.

**Gotcha:** The existing pipeline (line 1289) filters on `.htm` extension only. Make sure to use `.lower().endswith(".htm")` -- NOT `.html`. The pipeline's index.json entries use `.htm` for the main filing document.

**Gotcha:** `import time` is NOT currently at the top of `edgar_tools.py`. Add it.

**Gotcha:** `REQUEST_DELAY` is NOT currently imported in `edgar_tools.py`. Add it to the existing `from config import ...` line.

### Where to Place

Add the function after `build_filing_url()` (after line 246) and before `get_filings()` (line 249). This keeps all low-level helper functions grouped together.

### Test

```python
cd /Users/henrychien/Documents/Jupyter/edgar_updater
python -c "
from edgar_tools import fetch_filing_htm
html_bytes, url = fetch_filing_htm('0000320193', '0000320193-24-000123')
print(f'Got {len(html_bytes)} bytes from {url}')
assert len(html_bytes) > 10_000, 'Too small'
print('PASS')
"
```

Use a known AAPL accession. To find one quickly:
```python
python -c "
from edgar_tools import get_filings
result = get_filings('AAPL', 2024, 3)
print(result['filings'][0]['accession'])
"
```

Then use the printed accession and CIK `0000320193` in the `fetch_filing_htm` test.

---

## Phase 2: Create `section_parser.py`

### What

Create a new file `/Users/henrychien/Documents/Jupyter/edgar_updater/section_parser.py` with all section-parsing logic.

### Section Definitions

Define as module-level constants:

```python
SECTIONS_10K = {
    "item_1": r"Item\s*1[\.\:\s\u2013\u2014\-]+\s*Business",
    "item_1a": r"Item\s*1A[\.\:\s\u2013\u2014\-]+\s*Risk\s+Factors",
    "item_1b": r"Item\s*1B[\.\:\s\u2013\u2014\-]+\s*Unresolved\s+Staff\s+Comments",
    "item_2": r"Item\s*2[\.\:\s\u2013\u2014\-]+\s*Properties",
    "item_3": r"Item\s*3[\.\:\s\u2013\u2014\-]+\s*Legal\s+Proceedings",
    "item_7": r"Item\s*7[\.\:\s\u2013\u2014\-]+\s*Management.{0,5}s?\s+Discussion",
    "item_7a": r"Item\s*7A[\.\:\s\u2013\u2014\-]+\s*Quantitative\s+and\s+Qualitative",
    "item_8": r"Item\s*8[\.\:\s\u2013\u2014\-]+\s*Financial\s+Statements",
}

SECTIONS_10Q = {
    "part1_item1": r"(?:Part\s*I\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*1[\.\:\s\u2013\u2014\-]+\s*Financial\s+Statements",
    "part1_item2": r"(?:Part\s*I\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*2[\.\:\s\u2013\u2014\-]+\s*Management.{0,5}s?\s+Discussion",
    "part1_item3": r"(?:Part\s*I\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*3[\.\:\s\u2013\u2014\-]+\s*Quantitative",
    "part1_item4": r"(?:Part\s*I\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*4[\.\:\s\u2013\u2014\-]+\s*Controls\s+and\s+Procedures",
    "part2_item1": r"(?:Part\s*II\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*1[\.\:\s\u2013\u2014\-]+\s*Legal\s+Proceedings",
    "part2_item1a": r"(?:Part\s*II\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*1A[\.\:\s\u2013\u2014\-]+\s*Risk\s+Factors",
}

# Canonical ordering for document-order sorting
SECTION_ORDER_10K = ["item_1", "item_1a", "item_1b", "item_2", "item_3", "item_7", "item_7a", "item_8"]
SECTION_ORDER_10Q = ["part1_item1", "part1_item2", "part1_item3", "part1_item4", "part2_item1", "part2_item1a"]
```

### Imports

```python
import re
import os
import json
import time
from bs4 import BeautifulSoup, Tag

from config import HEADERS, REQUEST_DELAY, EXPORT_UPDATER_DIR
from edgar_tools import get_filings, fetch_filing_htm
# Note: lookup_cik_from_ticker is NOT needed — CIK is extracted from accession string
```

### Function 1: `fetch_filing_html(ticker, year, quarter) -> tuple[bytes, str, str]`

Thin wrapper that resolves ticker/year/quarter to raw HTML bytes. See spec lines 95-121.

```python
def fetch_filing_html(ticker: str, year: int, quarter: int) -> tuple[bytes, str, str]:
    """
    Fetch filing HTML using the existing pipeline.

    Returns: (html_bytes, filing_type, htm_url)

    Raises:
        ValueError: if no 10-K/10-Q filing found for the given period.
    """
```

Implementation:
1. Call `get_filings(ticker, year, quarter)`.
2. Check `result["status"] == "success"` and `result["filings"]` is non-empty.
3. Find the first filing where `f["form"]` is `"10-K"` or `"10-Q"` (skip 8-K entries).
4. Extract CIK from the accession number. The accession format is `{CIK}-{YY}-{SEQ}` (e.g., `0000320193-24-000123`), so `cik = filing["accession"].split("-")[0]`. This avoids a redundant `lookup_cik_from_ticker()` call, which makes an HTTP request to SEC (`utils.py:227-229`) and would add unnecessary latency and failure risk after `get_filings()` already succeeded.
5. Call `fetch_filing_htm(cik, filing["accession"])` to get `(html_bytes, htm_url)`.
6. Return `(html_bytes, filing["form"], htm_url)`.

**Gotcha:** `get_filings` returns 8-K entries too. Must filter to 10-K/10-Q only.

### Function 2: `parse_filing_sections(html_content, filing_type) -> dict`

This is the main entry point. Takes raw HTML bytes (or str) and the filing type (`"10-K"` or `"10-Q"`), returns the structured sections dict.

```python
def parse_filing_sections(html_content: bytes | str, filing_type: str) -> dict:
    """
    Parse an SEC filing HTML document into named sections.

    Args:
        html_content: Raw HTML bytes or string.
        filing_type: "10-K" or "10-Q".

    Returns:
        {
            "filing_type": str,
            "sections_found": list[str],
            "sections_missing": list[str],
            "sections": {
                "item_7": {
                    "header": "Item 7. Management's Discussion...",
                    "text": str,
                    "tables": list[str],
                    "word_count": int,
                }, ...
            },
            "metadata": {"total_word_count": int, "section_count": int}
        }
    """
```

Implementation:
1. Parse HTML with `BeautifulSoup(html_content, "lxml")`.
2. Select section definitions based on `filing_type` (`SECTIONS_10K` or `SECTIONS_10Q`).
3. Call `find_section_headers(soup, filing_type)` to get header locations.
4. Select section order based on `filing_type` (`SECTION_ORDER_10K` or `SECTION_ORDER_10Q`).
5. Call `extract_section_content(soup, headers, section_order)` to extract text between headers.
6. Compute `sections_found`, `sections_missing`, and `metadata`.
7. Return the structured dict.

### Function 3: `find_section_headers(soup, filing_type) -> list[dict]`

Scans the document for section headers. This is the most complex function.

```python
def find_section_headers(soup: BeautifulSoup, filing_type: str) -> list[dict]:
    """
    Find section header elements in the parsed HTML document.

    Returns a list of dicts, each with:
        - "key": section key (e.g., "item_7")
        - "element": the BeautifulSoup Tag
        - "header_text": the matched text
        - "position": document order index
    """
```

Implementation (Algorithm from spec lines 166-175):

1. Get section patterns based on `filing_type`.
2. Compile all regex patterns with `re.IGNORECASE`.
3. Walk all text-containing tags using `soup.find_all(text=True)` or iterate `soup.descendants`.
   - For each text node, get its parent tag.
   - Extract visible text, collapse whitespace with `re.sub(r'\s+', ' ', text).strip()`.
   - Test against each section regex pattern.
4. **TOC filtering** (critical for correctness):
   - Skip if the tag is inside an `<a href="#...">` (internal anchor link). Check with `tag.find_parent("a", href=re.compile(r"^#"))`.
   - Skip if the tag is inside a `<table>` that contains many `<a href="#...">` tags (TOC table heuristic). Check: if `tag.find_parent("table")` exists, count anchor links in that table; if > 5 anchor links, skip.
   - Skip if the tag's parent container has many other item-pattern matches clustered together (indicates a TOC block). To implement: track matches within the same parent `<div>` or `<td>`; if the parent already has 3+ matches, treat them all as TOC entries.
5. **Body-text filter**: Skip matches that are clearly in-body references:
   - If the matched text has more than ~15 words, it's a body sentence, not a header. Skip.
   - If the matched text starts with common reference words (`"see "`, `"refer to "`, `"as described in "`, `"discussed in "`, `"pursuant to "`), it's a cross-reference, not a header. Skip. (Case-insensitive prefix check on the full extracted text.)
6. **Deduplication**: If a section key appears multiple times after TOC and body-text filtering, keep the FIRST remaining occurrence. Since TOC entries have been filtered out, the first remaining match should be the real header. (Earlier versions of this plan said "keep LAST" but Codex review identified that body references near the end of a filing like "see Item 7" would override real headers under that strategy.)
6. Sort final list by document order (the `position` field).

**Gotcha:** Some filings use deeply nested HTML: `<b><font size="4"><p>Item 7...</p></font></b>`. The regex must match on the extracted TEXT regardless of tag nesting. Use `.get_text()` on the tag element, not the tag's direct string content.

**Gotcha:** 10-Q items are trickier because "Item 1" appears in both Part I and Part II with different meanings. The regex patterns must include optional "Part I" / "Part II" prefixes. If a match for "Item 1" is found without a Part prefix, use the following text ("Financial Statements" vs "Legal Proceedings") to disambiguate.

**Gotcha for 10-Q Part II Item 1 vs Part I Item 1:** The regex for `part2_item1` matches "Item 1" followed by "Legal Proceedings", while `part1_item1` matches "Item 1" followed by "Financial Statements". These are inherently different because of the description text. Make sure patterns are specific enough.

### Function 4: `extract_section_content(soup, headers, section_order) -> dict`

Extracts text between consecutive headers.

```python
def extract_section_content(soup: BeautifulSoup, headers: list[dict], section_order: list[str]) -> dict:
    """
    Extract content between consecutive section headers.

    Args:
        soup: Parsed HTML document.
        headers: Output of find_section_headers(), sorted by document order.
        section_order: Canonical section ordering list.

    Returns:
        dict mapping section_key -> {header, text, tables, word_count}
    """
```

Implementation (single canonical approach):

1. Walk `soup.descendants` once, recording all Tag elements in document order as a flat list with integer indices.
2. Map each header's element to its index in this flat list.
3. For consecutive headers at indices `i` and `j`, the content is all elements from index `i+1` to `j-1`.
4. Collect those elements and pass to `html_to_text()` for conversion.
5. For the LAST header, content runs from its index to the end of the document (or to a reasonable cutoff).
6. For each slice, extract `<table>` elements and convert via `table_to_markdown()`. Convert remaining content via `html_to_text()`.
7. Count words: `len(text.split())`.
8. Return the dict mapping section_key -> {header, text, tables, word_count}.

This is the only approach to use. Headers may not be siblings (different nesting levels), so `.find_next_siblings()` would miss content in different DOM branches. Do NOT use alternative approaches (string-position slicing, sourceline, etc.).

### Function 5: `html_to_text(element) -> str`

Converts HTML fragment to clean, markdown-like text.

```python
def html_to_text(element) -> str:
    """
    Convert an HTML element (or list of elements) to clean markdown-like text.
    """
```

Implementation (spec lines 187-191):
- `<table>` -> call `table_to_markdown()` on the table, include result as text.
- `<p>`, `<div>` -> double newline (paragraph break).
- `<li>` -> prefix with `"- "`.
- `<b>`, `<strong>` -> wrap text in `**...**`.
- `<h1>` through `<h6>` -> prefix with `#` repeated.
- `<br>` -> single newline.
- Collapse 3+ consecutive newlines to 2.
- Strip per-line leading/trailing whitespace.

This function should walk the element tree recursively. For each tag, apply formatting rules. For text nodes, emit the text. Build a string incrementally.

**Gotcha:** Must handle `<table>` elements specially -- extract them as standalone markdown tables and do NOT recurse into their children for inline text extraction (that would produce garbled output).

### Function 6: `table_to_markdown(table_tag) -> str`

Converts an HTML `<table>` to a pipe-delimited markdown table.

```python
def table_to_markdown(table_tag: Tag) -> str:
    """
    Convert an HTML <table> tag to a markdown-formatted table string.
    """
```

Implementation:
1. Find all `<tr>` rows.
2. For each row, find all `<td>` and `<th>` cells.
3. Extract text from each cell (`.get_text(strip=True)`).
4. Join cells with ` | ` and prefix/suffix with `|`.
5. After the first row (header), insert a separator row (`| --- | --- | ...`).
6. Return the complete markdown table string.

**Gotcha:** Many SEC filing tables have irregular structures -- `colspan`, `rowspan`, empty rows. Handle gracefully: skip completely empty rows, treat `colspan` as merging cells.

**Gotcha:** Some tables are enormous (Item 8 Financial Statements). Do not try to be perfect -- readable is good enough.

### Function 7: `_truncate(text, max_words) -> str`

Helper that truncates text to `max_words` and appends a continuation marker. Follows the protocol pattern from `PROTOCOL_agent_tool_responses.md`.

```python
def _truncate(text: str, max_words: int | None) -> str:
    """Truncate text to max_words, appending continuation marker."""
    if max_words is None:
        return text
    words = text.split()
    if len(words) <= max_words:
        return text
    remaining = len(words) - max_words
    return " ".join(words[:max_words]) + f"\n\n...[truncated — {remaining:,} more words remaining]"
```

### Function 8: `get_filing_sections_cached(ticker, year, quarter, sections, format, max_words) -> dict`

Cache wrapper that checks for existing cached results. Applies format/truncation logic per the agent-tool response protocol.

```python
def get_filing_sections_cached(
    ticker: str,
    year: int,
    quarter: int,
    sections: list[str] | None = None,
    format: str = "summary",
    max_words: int | None = 3000,
) -> dict:
    """
    Get filing sections with file-based caching.

    Cache file: exports/{TICKER}_{Q}Q{YY}_sections.json

    Args:
        ticker: Stock ticker (e.g., "AAPL").
        year: Fiscal year.
        quarter: Quarter 1-4.
        sections: Optional list of section keys to return. If None, return all.
        format: "summary" (default) returns metadata only (section names, word counts,
                filing type). "full" returns text content, subject to max_words truncation.
        max_words: Max words per section text field (default 3000). None = unlimited.

    Returns:
        Same structure as parse_filing_sections(), optionally filtered to
        requested sections, with format/truncation applied.
    """
```

Implementation:
1. Build cache path: `os.path.join(EXPORT_UPDATER_DIR, f"{ticker.upper()}_{quarter}Q{str(year)[-2:]}_sections.json")`.
2. If cache file exists, load it. (Cache stores the raw parse result — no `status` key.)
3. If no cache, call `fetch_filing_html()` to get HTML, then `parse_filing_sections()`.
4. Save the FULL (unfiltered, untruncated) result to cache. **Cache stores raw parse output only** (no `status`, no filtering). The `status` key is added by the caller (`edgar_tools.get_filing_sections()`), not by this function.
5. **Deep-copy** the result before applying filters (cache may be reused).
6. If `sections` filter is provided, filter the result to only include those section keys.
7. **Recompute metadata after filtering**: Update `sections_found` to list only returned section keys. Recompute `sections_missing` to exclude filtered-out sections. Recompute `metadata.total_word_count` and `metadata.section_count` to reflect only the returned sections.
8. **Apply format logic:**
   - If `format="summary"`: strip `text` and `tables` fields from each section, keep only `header`, `word_count`. Add a top-level `hint` field: `"Use format='full' with sections=['item_7'] to get text for a specific section."`.
   - If `format="full"` and `sections` is provided: apply `_truncate(text, max_words)` to each section's `text` field.
   - If `format="full"` and `sections` is **omitted** (unfiltered full request): return a **preview** instead -- first ~500 words of each section (via `_truncate(text, 500)`) plus word counts, with a `hint` field: `"Specify sections=['item_7'] to get full text for a specific section."`. This prevents dumping an entire 50K-word filing into context.
9. Return result. (**Note:** This function does NOT add `status` — the caller wraps it.)

**Gotcha:** Always cache the FULL result (all sections), then filter on read. This avoids re-parsing when a different subset of sections is requested.

**Gotcha:** The `EXPORT_UPDATER_DIR` from `config.py` is `"exports"` -- a relative path. This is consistent with existing cache usage in the codebase.

### Test (Phase 2)

Test parsing directly (no network calls needed if you save an HTML file):

```python
cd /Users/henrychien/Documents/Jupyter/edgar_updater
python -c "
from section_parser import fetch_filing_html, parse_filing_sections

# Fetch HTML
html_bytes, filing_type, url = fetch_filing_html('AAPL', 2024, 3)
print(f'Fetched {len(html_bytes)} bytes, type={filing_type}, url={url}')

# Parse sections
result = parse_filing_sections(html_bytes, filing_type)
print(f'Found sections: {result[\"sections_found\"]}')
print(f'Missing sections: {result[\"sections_missing\"]}')
for key, section in result['sections'].items():
    print(f'  {key}: {section[\"word_count\"]} words, {len(section[\"tables\"])} tables')
    # Print first 200 chars of text
    print(f'    Preview: {section[\"text\"][:200]}')
print(f'Total words: {result[\"metadata\"][\"total_word_count\"]}')
"
```

Expected: For AAPL Q3 2024 (a 10-Q), should find `part1_item2` (MD&A) with thousands of words.

Test TOC filtering specifically:
```python
python -c "
from section_parser import fetch_filing_html, parse_filing_sections
html_bytes, filing_type, url = fetch_filing_html('AAPL', 2024, 3)
result = parse_filing_sections(html_bytes, filing_type)
# MD&A should have substantial content, not just a TOC entry
mda = result['sections'].get('part1_item2', {})
assert mda.get('word_count', 0) > 500, f'MD&A too short ({mda.get(\"word_count\", 0)} words) -- likely picked up TOC instead of real header'
print(f'MD&A: {mda[\"word_count\"]} words -- PASS')
"
```

Test with additional tickers for robustness:
```python
# JPM (complex MD&A, large filing) — summary mode (default)
python -c "from section_parser import get_filing_sections_cached; r = get_filing_sections_cached('JPM', 2024, 3); print(r['sections_found']); print('hint:', r.get('hint'))"

# TSLA (unusual formatting) — summary mode (default)
python -c "from section_parser import get_filing_sections_cached; r = get_filing_sections_cached('TSLA', 2024, 3); print(r['sections_found']); print('hint:', r.get('hint'))"

# Full text with section filter and truncation
python -c "from section_parser import get_filing_sections_cached; r = get_filing_sections_cached('JPM', 2024, 3, sections=['part1_item2'], format='full', max_words=3000); print(f'MD&A: {len(r[\"sections\"][\"part1_item2\"][\"text\"].split())} words (truncated)')"
```

---

## Phase 3: Add `get_filing_sections()` wrapper to `edgar_tools.py`

### What

Add a `get_filing_sections()` function to `/Users/henrychien/Documents/Jupyter/edgar_updater/edgar_tools.py` as a thin wrapper around `section_parser.get_filing_sections_cached()`.

### Function Signature

```python
def get_filing_sections(
    ticker: str,
    year: int,
    quarter: int,
    sections: list[str] | None = None,
    format: str = "summary",
    max_words: int | None = 3000,
) -> dict:
    """
    Parse qualitative sections from SEC 10-K or 10-Q filings.

    This is the public API entry point. Validates ticker, then delegates
    to section_parser.get_filing_sections_cached().

    Defaults to format="summary" (metadata only: section names, word counts,
    filing type). Use format="full" with a sections filter to get text content.

    Returns structured dict with section text, tables, and word counts.
    """
```

### Implementation

```python
def get_filing_sections(
    ticker: str,
    year: int,
    quarter: int,
    sections: list[str] | None = None,
    format: str = "summary",
    max_words: int | None = 3000,
) -> dict:
    # Validate ticker (same pattern as get_filings/get_financials)
    if err := _validate_ticker(ticker):
        return {"status": "error", "message": err}

    try:
        from section_parser import get_filing_sections_cached
        result = get_filing_sections_cached(
            ticker, year, quarter, sections,
            format=format, max_words=max_words,
        )
        return {"status": "success", **result}
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Section parsing failed: {str(e)}"}
```

### Where to Place

Add after `get_metric()` (after line 607), at the end of the file. This keeps it grouped with the other public API functions.

### Import

Use a lazy import (`from section_parser import ...` inside the function body) to avoid circular imports and keep the module load fast. This matches the pattern used for `edgar_8k` imports elsewhere in `edgar_tools.py` (line 308, 357).

### Test

```python
cd /Users/henrychien/Documents/Jupyter/edgar_updater

# Default call — summary mode (metadata only, no text content)
python -c "
from edgar_tools import get_filing_sections
result = get_filing_sections('AAPL', 2024, 3)
print(f'Status: {result[\"status\"]}')
print(f'Sections found: {result[\"sections_found\"]}')
print(f'Hint: {result.get(\"hint\", \"(none)\")}')
# Verify summary mode: sections should have word_count but NOT text
for key, sec in result['sections'].items():
    assert 'word_count' in sec, f'{key} missing word_count'
    assert 'text' not in sec, f'{key} should not have text in summary mode'
    print(f'  {key}: {sec[\"word_count\"]} words')
print('PASS — summary mode returns metadata only')
"
```

Test full mode with section filter (the recommended workflow):
```python
python -c "
from edgar_tools import get_filing_sections
result = get_filing_sections('AAPL', 2024, 3, sections=['part1_item2'], format='full')
print(f'Sections: {list(result[\"sections\"].keys())}')  # Should only have 'part1_item2'
mda = result['sections']['part1_item2']
words = mda['text'].split()
print(f'MD&A text: {len(words)} words (truncated to max_words=3000)')
assert len(words) <= 3100, 'Text should be truncated to ~3000 words'
print('PASS — full mode with section filter + truncation')
"
```

Test unfiltered full request (should return preview, not full dump):
```python
python -c "
from edgar_tools import get_filing_sections
result = get_filing_sections('AAPL', 2024, 3, format='full')
print(f'Hint: {result.get(\"hint\", \"(none)\")}')
# Each section should be preview-truncated to ~500 words
for key, sec in result['sections'].items():
    words = sec['text'].split()
    assert len(words) <= 600, f'{key} should be preview-truncated (~500 words), got {len(words)}'
    print(f'  {key}: {len(words)} words (preview), actual: {sec[\"word_count\"]} words')
print('PASS — unfiltered full returns preview with hint')
"
```

Test max_words override:
```python
python -c "
from edgar_tools import get_filing_sections
result = get_filing_sections('AAPL', 2024, 3, sections=['part1_item2'], format='full', max_words=500)
mda = result['sections']['part1_item2']
words = mda['text'].split()
print(f'MD&A with max_words=500: {len(words)} words')
assert len(words) <= 600, f'Expected ~500 words, got {len(words)}'
assert 'truncated' in mda['text'], 'Should have truncation marker'
print('PASS — max_words override works')
"
```

Test error handling:
```python
python -c "
from edgar_tools import get_filing_sections
# Invalid ticker
result = get_filing_sections('ZZZZZ', 2024, 3)
print(f'Status: {result[\"status\"]}')  # Should be 'error'
print(f'Message: {result[\"message\"]}')
"
```

---

## Phase 4: Register MCP tool in `mcp_server.py`

### What

Add the `get_filing_sections` tool to the MCP server at `/Users/henrychien/Documents/Jupyter/edgar_updater/mcp_server.py`.

### Changes

**1. Add import (line 13):**

Change:
```python
from edgar_tools import get_filings, get_financials, get_metric
```
To:
```python
from edgar_tools import get_filings, get_financials, get_metric, get_filing_sections
```

**2. Add Tool definition to `list_tools()` (after line 106, before the closing `]`):**

```python
        Tool(
            name="get_filing_sections",
            description=(
                "Parse qualitative sections from SEC 10-K or 10-Q filings. "
                "Returns narrative text (Risk Factors, MD&A, Business Description, etc.) "
                "with clean text, embedded tables, and word counts.\n\n"
                "Recommended workflow:\n"
                "1. Call with default format='summary' to see available sections and word counts.\n"
                "2. Identify the section(s) you need.\n"
                "3. Call again with format='full' and sections=['item_7'] to get text for specific sections.\n\n"
                "Defaults to summary mode (metadata only — no text content). "
                "Full text is truncated to max_words (default 3000) per section. "
                "If format='full' is used without a sections filter, returns a preview (~500 words per section) "
                "to avoid overwhelming context.\n\n"
                "10-K sections: item_1 (Business), item_1a (Risk Factors), item_7 (MD&A), etc. "
                "10-Q sections: part1_item2 (MD&A), part2_item1a (Risk Factors), etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker symbol (e.g., AAPL)",
                    },
                    "year": {
                        "type": "integer",
                        "description": "Fiscal year (e.g., 2024)",
                    },
                    "quarter": {
                        "type": "integer",
                        "description": "Quarter 1-4",
                    },
                    "sections": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of section keys to return. "
                            "If omitted, returns all sections. "
                            "10-K keys: item_1, item_1a, item_1b, item_2, item_3, item_7, item_7a, item_8. "
                            "10-Q keys: part1_item1, part1_item2, part1_item3, part1_item4, part2_item1, part2_item1a."
                        ),
                    },
                    "format": {
                        "type": "string",
                        "enum": ["summary", "full"],
                        "description": (
                            "Output format. 'summary' (default) returns metadata only: "
                            "section names, word counts, filing type — no text content. "
                            "'full' returns section text, subject to max_words truncation."
                        ),
                    },
                    "max_words": {
                        "type": ["integer", "null"],
                        "description": (
                            "Max words per section text field (default 3000). "
                            "Only applies when format='full'. "
                            "Set to null for unlimited (use with caution — large sections can exceed 10K words)."
                        ),
                    },
                },
                "required": ["ticker", "year", "quarter"],
            },
        ),
```

**3. Add handler in `call_tool()` (after line 135, before the `else` branch):**

```python
    elif name == "get_filing_sections":
        result = get_filing_sections(
            ticker=arguments["ticker"],
            year=arguments["year"],
            quarter=arguments["quarter"],
            sections=arguments.get("sections"),
            format=arguments.get("format", "summary"),
            max_words=arguments.get("max_words", 3000),
        )
```

### Test

Restart the MCP server and test via Claude Code or direct MCP call:

```bash
# Restart the MCP server (kill existing process, then restart)
# Then in Claude Code, test the recommended workflow:

# Step 1: Summary call (default) — see structure and word counts
# get_filing_sections(ticker="AAPL", year=2024, quarter=3)

# Step 2: Full text for a specific section
# get_filing_sections(ticker="AAPL", year=2024, quarter=3, sections=["part1_item2"], format="full")

# Step 3: Full text with custom max_words
# get_filing_sections(ticker="AAPL", year=2024, quarter=3, sections=["part1_item2"], format="full", max_words=5000)
```

Or test directly:
```python
cd /Users/henrychien/Documents/Jupyter/edgar_updater

# Test summary mode (default)
python -c "
import asyncio, json
from mcp_server import call_tool
result = asyncio.run(call_tool('get_filing_sections', {'ticker': 'AAPL', 'year': 2024, 'quarter': 3}))
print(result[0].text[:500])
"

# Test full mode with section filter
python -c "
import asyncio, json
from mcp_server import call_tool
result = asyncio.run(call_tool('get_filing_sections', {'ticker': 'AAPL', 'year': 2024, 'quarter': 3, 'sections': ['part1_item2'], 'format': 'full'}))
print(result[0].text[:500])
"
```

---

## Phase 5: Add Flask Route to `app.py` (Optional)

### What

Add a `/api/sections` GET endpoint to `/Users/henrychien/Documents/Jupyter/edgar_updater/app.py`.

### Where

Add after the `/api/metric` route (after line 921) and before the web UI form route (line 924).

### Implementation

Follow the exact same patterns as `/api/filings` (lines 717-776):

```python
@app.route("/api/sections", methods=["GET"])
@limiter.limit(
    limit_value=lambda: RATE_LIMITS[TIER_MAP.get(request.args.get("key", "public"), "public")],
    deduct_when=lambda response: response.status_code == 200
)
def api_sections():
    """
    JSON API endpoint for parsing qualitative sections from 10-K/10-Q filings.
    """
    user_key = request.args.get("key", PUBLIC_KEY)
    user_tier = TIER_MAP.get(user_key, "public")

    # 1. Validate API key
    if user_key not in VALID_KEYS:
        log_request(None, None, None, user_key, "api_json", "denied", user_tier, full_year_mode=False)
        return jsonify({"status": "error", "message": "Invalid API key"}), 401

    # 2. Parse and validate parameters
    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker or ticker not in VALID_TICKERS:
        log_request(ticker, None, None, user_key, "api_json", "denied", user_tier, full_year_mode=False)
        return jsonify({"status": "error", "message": f"Invalid or unsupported ticker: {ticker}"}), 400

    try:
        year = int(request.args.get("year", ""))
        quarter = int(request.args.get("quarter", ""))
        if not (1 <= quarter <= 4):
            raise ValueError("Quarter must be between 1 and 4")
    except (ValueError, TypeError):
        log_request(ticker, None, None, user_key, "api_json", "denied", user_tier, full_year_mode=False)
        return jsonify({"status": "error", "message": "Invalid year or quarter format"}), 400

    # Parse optional sections filter
    sections_param = request.args.get("sections", "")
    sections = [s.strip() for s in sections_param.split(",") if s.strip()] if sections_param else None

    # Parse format and max_words (defaults match protocol: summary mode, 3000 word truncation)
    format_param = request.args.get("format", "summary")
    if format_param not in ("summary", "full"):
        return jsonify({"status": "error", "message": "format must be 'summary' or 'full'"}), 400
    max_words_param = request.args.get("max_words", "3000")
    try:
        max_words = int(max_words_param) if max_words_param.lower() != "none" else None
    except ValueError:
        return jsonify({"status": "error", "message": "max_words must be an integer or 'none'"}), 400

    # 3. Always delegate to get_filing_sections() — it handles caching internally.
    # (No cache-hit shortcut in Flask route. This avoids duplicating format/truncation
    # logic and prevents drift between the Flask route and the core function.)
    try:
        from edgar_tools import get_filing_sections
        result = get_filing_sections(
            ticker, year, quarter, sections,
            format=format_param, max_words=max_words,
        )

        log_request(ticker, year, quarter, user_key, "api_json", "success", user_tier, full_year_mode=False)
        return jsonify(result), 200

    except Exception as e:
        log_error_json("API_SECTIONS", {
            "ticker": ticker,
            "year": year,
            "quarter": quarter,
            "full_year_mode": False
        }, e, key=user_key, tier=user_tier)
        return jsonify({"status": "error", "message": str(e)}), 500
```

**Gotcha:** No `pipeline_lock` needed. Section parsing does not run the full iXBRL pipeline. It only fetches a single HTML file from SEC and parses it locally. The SEC rate limiting (1 req/sec via `REQUEST_DELAY`) is sufficient.

**Gotcha:** The `sections` parameter should be comma-separated in the URL: `/api/sections?ticker=AAPL&year=2024&quarter=3&sections=part1_item2,part2_item1a&format=full`.

### Update app.py Docstring

Add to the endpoints list at the top of the file:

```
    /api/sections           GET - returns parsed qualitative sections (Risk Factors, MD&A, etc.)
                            Params: ticker, year, quarter, sections (optional, comma-separated),
                                    format (summary|full, default summary), max_words (int, default 3000)
```

### Test

```bash
# Summary mode (default) — metadata only
curl "http://localhost:5000/api/sections?ticker=AAPL&year=2024&quarter=3&key=PUBLIC_KEY"

# Full text for a specific section
curl "http://localhost:5000/api/sections?ticker=AAPL&year=2024&quarter=3&key=PUBLIC_KEY&sections=part1_item2&format=full"

# Full text with custom max_words
curl "http://localhost:5000/api/sections?ticker=AAPL&year=2024&quarter=3&key=PUBLIC_KEY&sections=part1_item2&format=full&max_words=5000"
```

---

## Key Implementation Gotchas Summary

1. **Nested functions in `edgar_pipeline.py`:** Do NOT try to import or extract functions from `edgar_pipeline.py`. They are nested inside `run_edgar_pipeline()`. Duplicate the minimal logic needed (just the index.json fetch) in `edgar_tools.py`.

2. **TOC vs real headers:** The hardest part of section parsing. Many filings have a Table of Contents that lists the same section headers as anchor links. After TOC filtering and body-text filtering (>15 words), keep the FIRST remaining match per section key.

3. **10-Q Part disambiguation:** "Item 1" appears in both Part I and Part II of 10-Q filings with different meanings. The regex patterns MUST differentiate by the descriptive text that follows (e.g., "Financial Statements" vs "Legal Proceedings").

4. **BeautifulSoup parser:** Use `"lxml"` parser (same as `edgar_pipeline.py` line 1172) for consistency and performance.

5. **Encoding:** SEC filings are typically UTF-8 but sometimes Windows-1252. `BeautifulSoup` handles encoding detection automatically when given bytes. Pass `html_content` as bytes when possible.

6. **Large filings:** Item 8 (Financial Statements) can be enormous (thousands of tables). The parser will be slow but functional. Consider truncating Item 8 content or adding a warning about its size.

7. **Cache consistency:** The cache file should always contain the FULL parsed result (all sections, full text, no truncation). Filtering, format stripping, and truncation are applied at read time, never at write time. This ensures the cache can serve any combination of `format`, `sections`, and `max_words` without re-parsing.

8. **`EXPORT_UPDATER_DIR` vs `EXPORT_DIR`:** In `config.py` it is `EXPORT_UPDATER_DIR = "exports"`. In `app.py` it is locally defined as `EXPORT_DIR = "exports"`. They are the same directory. Use `EXPORT_UPDATER_DIR` from config in `section_parser.py` for consistency with `config.py` usage patterns.

9. **Summary mode strips text fields.** When `format="summary"`, the response must NOT include `text` or `tables` keys in each section dict. Only `header` and `word_count` should remain. This keeps the summary response under ~1 KB per the protocol guidelines.

10. **Unfiltered full request returns preview, not full dump.** If `format="full"` and `sections` is omitted, each section's text is truncated to ~500 words (not `max_words`) and a top-level `hint` is added. This is a safeguard: an agent requesting all sections in full mode would otherwise receive 30-50K words, which degrades reasoning quality. The preview gives the agent enough context to decide which sections to drill into.

11. **Truncation marker format.** Use the exact format: `"\n\n...[truncated — {N:,} more words remaining]"` (with comma separator in the number). This is standardized across all tools per the protocol.

12. **Spec vs plan discrepancy on `edgar_pipeline.py`.** The spec (`SPEC_section_parser.md`) suggests refactoring `edgar_pipeline.py` to use the shared `fetch_filing_htm()` helper. This plan intentionally does NOT modify `edgar_pipeline.py` to avoid risk to the working iXBRL pipeline. The shared helper duplicates minimal logic (index.json fetching + .htm selection). This can be revisited later as a separate refactor task.

13. **Response schema consistency.** `get_filing_sections_cached()` returns the raw parse result (no `status` key). The `status` key is added by `edgar_tools.get_filing_sections()` which wraps the result as `{"status": "success", **result}`. The Flask route delegates entirely to `get_filing_sections()`, so all callers always get a consistent `status` field.

14. **Tests require network access.** The parse tests in Phase 2 call `fetch_filing_html()` which hits SEC EDGAR. These are not purely offline tests. For offline testing, save an HTML file first and test `parse_filing_sections()` directly on the saved bytes.

---

## Files Summary

| File | Action | Phase |
|------|--------|-------|
| `edgar_tools.py` | Add `fetch_filing_htm()` + add `import time` + add `REQUEST_DELAY` import | Phase 1 |
| `section_parser.py` | **NEW** -- all section parsing logic + `_truncate()` helper + format/max_words support in `get_filing_sections_cached()` | Phase 2 |
| `edgar_tools.py` | Add `get_filing_sections()` wrapper with `format` and `max_words` params | Phase 3 |
| `mcp_server.py` | Add tool definition (with `format`, `max_words` in inputSchema, workflow coaching in description) + handler | Phase 4 |
| `app.py` | Add `/api/sections` route (optional) | Phase 5 |
| `edgar_pipeline.py` | **NO CHANGES** -- leave untouched | N/A |
| `config.py` | **NO CHANGES** | N/A |
| `utils.py` | **NO CHANGES** | N/A |

---

## Verification Checklist

After all phases complete, run these checks:

```bash
cd /Users/henrychien/Documents/Jupyter/edgar_updater

# 1. Summary mode (default) — metadata only, no text
python -c "
from section_parser import get_filing_sections_cached
r = get_filing_sections_cached('AAPL', 2024, 3)
print(f'Sections: {r[\"sections_found\"]}')
print(f'Hint: {r.get(\"hint\")}')
for k, s in r['sections'].items():
    assert 'text' not in s, f'{k} has text in summary mode'
    print(f'  {k}: {s[\"word_count\"]} words')
print('PASS — summary mode')
"

# 2. Full mode with section filter — text with truncation
python -c "
from section_parser import get_filing_sections_cached
r = get_filing_sections_cached('AAPL', 2024, 3, sections=['part1_item2'], format='full', max_words=3000)
mda = r['sections']['part1_item2']
print(f'MD&A: {mda[\"word_count\"]} words total, text returned: {len(mda[\"text\"].split())} words')
print('PASS — full mode with filter')
"

# 3. Unfiltered full request — preview mode (~500 words per section + hint)
python -c "
from section_parser import get_filing_sections_cached
r = get_filing_sections_cached('AAPL', 2024, 3, format='full')
print(f'Hint: {r.get(\"hint\")}')
for k, s in r['sections'].items():
    print(f'  {k}: preview {len(s[\"text\"].split())} words, actual {s[\"word_count\"]} words')
print('PASS — unfiltered full returns preview')
"

# 4. edgar_tools wrapper test (summary mode default)
python -c "from edgar_tools import get_filing_sections; r = get_filing_sections('AAPL', 2024, 3); print(r['status'], r.get('sections_found'), r.get('hint'))"

# 5. Cache test (second call should be instant)
python -c "
import time
from edgar_tools import get_filing_sections
t0 = time.time(); get_filing_sections('AAPL', 2024, 3); t1 = time.time()
t2 = time.time(); get_filing_sections('AAPL', 2024, 3); t3 = time.time()
print(f'First call: {t1-t0:.1f}s, Second call (cached): {t3-t2:.3f}s')
"

# 6. 10-K test (quarter=4 returns 10-K)
python -c "from edgar_tools import get_filing_sections; r = get_filing_sections('AAPL', 2024, 4); print(r['status'], r.get('sections_found'))"

# 7. Error handling test
python -c "from edgar_tools import get_filing_sections; r = get_filing_sections('ZZZZZ', 2024, 3); print(r['status'], r.get('message'))"

# 8. MCP test (after restarting MCP server) — recommended workflow
# Step 1: get_filing_sections(ticker="AAPL", year=2024, quarter=3)
#   -> summary with word counts and hint
# Step 2: get_filing_sections(ticker="AAPL", year=2024, quarter=3, sections=["part1_item2"], format="full")
#   -> full text for MD&A, truncated to 3000 words
```

### Critical Files for Implementation

- `/Users/henrychien/Documents/Jupyter/edgar_updater/edgar_tools.py` -- Add `fetch_filing_htm()` (Phase 1) and `get_filing_sections()` wrapper (Phase 3)
- `/Users/henrychien/Documents/Jupyter/edgar_updater/section_parser.py` -- New file: core parsing logic with all 8 functions including `_truncate()` helper (Phase 2)
- `/Users/henrychien/Documents/Jupyter/edgar_updater/mcp_server.py` -- Register the MCP tool (Phase 4)
- `/Users/henrychien/Documents/Jupyter/edgar_updater/app.py` -- Add `/api/sections` route (Phase 5, optional)
- `/Users/henrychien/Documents/Jupyter/edgar_updater/edgar_pipeline.py` -- Reference only: lines 1270-1296 contain the index.json logic to duplicate; DO NOT modify this file
- `/Users/henrychien/Documents/Jupyter/edgar_updater/docs/SPEC_section_parser.md` -- The specification document that governs all design decisions

---

## Review History

### Round 1 — Codex Review (2026-02-07)
- 3 HIGH, 6 MED, 1 LOW findings
- All addressed in plan update:
  1. HIGH: Cache/status schema inconsistency — clarified that cache stores raw parse output, `status` added by caller
  2. HIGH: `fetch_filing_htm()` missing fallback — added fallback to all .htm files when size metadata absent
  3. HIGH: Header detection body-text matches — added 15-word filter, changed dedupe from keep-LAST to keep-FIRST
  4. MED: Spec vs plan conflict on edgar_pipeline.py — added explicit note explaining decision (Gotcha #12)
  5. MED: Flask cache-hit shortcut — removed, always delegates to `get_filing_sections()`
  6. MED: MCP schema max_words null — changed type to `["integer", "null"]`
  7. MED: Redundant CIK lookup — documented that `lookup_cik_from_ticker` is a local lookup, acceptable
  8. MED: Section filtering metadata — added metadata recomputation step after filtering
  9. MED: Content extraction conflicting approaches — consolidated to single canonical approach (flat descendants list)
  10. LOW: Test network assumptions — added Gotcha #14 noting tests require SEC EDGAR access

### Round 2 — Codex Review (2026-02-07)
- 2 HIGH, 2 MED remaining findings
- All addressed:
  1. HIGH: fetch_filing_htm fallback still narrower than pipeline — changed to `sized + unsized` candidate list so unsized .htm files always get tried
  2. HIGH: CIK lookup is HTTP, not local — fixed to extract CIK from accession string (`accession.split("-")[0]`), no second HTTP call
  3. MED: Content extraction still had conflicting descriptions — removed the old sibling/following description, kept only flat descendants approach
  4. MED: Short body references pass 15-word filter — added prefix check for "See ", "Refer to ", etc.
