# SPEC: 10-K/10-Q Section Reader

## Goal

Parse SEC filing HTML into named sections so an AI agent can read qualitative content — Business description, Risk Factors, MD&A, etc.

## New File

`section_parser.py`

## Files to Modify

- `edgar_tools.py` — add `fetch_filing_htm()` shared helper (extracted from pipeline) + `get_filing_sections()` wrapper function
- `edgar_pipeline.py` — refactor `try_all_htm_files()` to call the new `fetch_filing_htm()` helper
- `mcp_server.py` — register `get_filing_sections` MCP tool
- `app.py` — optionally add `/api/sections` route

## Section Definitions

**10-K sections:**
- `item_1`: Business
- `item_1a`: Risk Factors
- `item_1b`: Unresolved Staff Comments
- `item_2`: Properties
- `item_3`: Legal Proceedings
- `item_7`: MD&A
- `item_7a`: Quantitative and Qualitative Disclosures About Market Risk
- `item_8`: Financial Statements

**10-Q sections:**
- `part1_item1`: Financial Statements
- `part1_item2`: MD&A
- `part1_item3`: Quantitative and Qualitative Disclosures
- `part1_item4`: Controls and Procedures
- `part2_item1`: Legal Proceedings
- `part2_item1a`: Risk Factors

## Document Fetching Strategy

**Reuse the existing pipeline** rather than reimplementing document discovery:

1. **Accession resolution:** Call `edgar_tools.get_filings(ticker, year, quarter)` to resolve ticker/year/quarter → accession number + filing type (10-K or 10-Q). This already handles CIK lookup, accession fetching, fiscal quarter labeling, and validation.

2. **Document discovery:** Extract the index.json → largest .htm logic from `edgar_pipeline.try_all_htm_files()` into a new shared helper in `edgar_tools.py`:

```python
# edgar_tools.py — NEW helper

def fetch_filing_htm(cik: str, accession: str) -> tuple[bytes, str]:
    """
    Fetch the main .htm file from an SEC filing accession.

    Uses the same strategy as try_all_htm_files(): fetch index.json,
    sort .htm files by size descending, download the largest one.
    Falls back to next-largest if download fails.

    Returns:
        (html_bytes, htm_url) — raw HTML content and the URL it came from

    Raises:
        ValueError if no valid .htm file found in accession.
    """
    acc_nodash = accession.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/index.json"
    base_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/"

    r = requests.get(index_url, headers=HEADERS)
    time.sleep(REQUEST_DELAY)
    r.raise_for_status()

    items = r.json().get("directory", {}).get("item", [])
    htm_items = [
        item for item in items
        if item["name"].lower().endswith(".htm") and item.get("size", "").isdigit()
    ]
    htm_items.sort(key=lambda x: int(x["size"]), reverse=True)

    for item in htm_items:
        url = base_url + item["name"]
        resp = requests.get(url, headers=HEADERS)
        time.sleep(REQUEST_DELAY)
        if resp.ok and len(resp.content) > 10_000:  # skip tiny exhibits
            return resp.content, url

    raise ValueError(f"No valid .htm file found in {accession}")
```

3. **Refactor `try_all_htm_files()`** to call `fetch_filing_htm()` internally, so the iXBRL pipeline also benefits from the shared helper. This avoids code duplication.

The section parser's `fetch_filing_html()` then becomes a thin wrapper:

```python
# section_parser.py

def fetch_filing_html(ticker: str, year: int, quarter: int) -> tuple[bytes, str, str]:
    """
    Fetch filing HTML using the existing pipeline.

    Returns: (html_bytes, filing_type, htm_url)
    """
    from edgar_tools import get_filings, fetch_filing_htm
    from utils import lookup_cik_from_ticker

    result = get_filings(ticker, year, quarter)
    if result["status"] != "success" or not result["filings"]:
        raise ValueError(f"No filing found for {ticker} {quarter}Q{year}")

    # Prefer 10-K/10-Q over 8-K
    filing = next(
        (f for f in result["filings"] if f["form"] in ("10-K", "10-Q")),
        None
    )
    if not filing:
        raise ValueError(f"No 10-K/10-Q found for {ticker} {quarter}Q{year}")

    cik = lookup_cik_from_ticker(ticker)
    html_bytes, htm_url = fetch_filing_htm(cik, filing["accession"])
    filing_type = filing["form"]  # "10-K" or "10-Q"

    return html_bytes, filing_type, htm_url
```

## Core Functions

```python
# section_parser.py

def parse_filing_sections(html_content: bytes | str, filing_type: str) -> dict:
    """Main entry point. Returns structured sections from filing HTML."""
    # Returns:
    # {
    #     "filing_type": str,
    #     "sections_found": list[str],
    #     "sections_missing": list[str],
    #     "sections": {
    #         "item_7": {
    #             "header": "Item 7. Management's Discussion...",
    #             "text": str,        # Clean markdown-like text
    #             "tables": list[str], # Markdown tables in section
    #             "word_count": int,
    #         }, ...
    #     },
    #     "metadata": {"total_word_count": int, "section_count": int}
    # }

def find_section_headers(soup: BeautifulSoup, filing_type: str) -> list[dict]:
    """Scan HTML for section headers using regex patterns. Filter out TOC links."""

def extract_section_content(soup, headers, section_order) -> dict:
    """Extract text between consecutive section headers."""

def html_to_text(element: Tag) -> str:
    """Convert HTML to clean markdown-like text. Tables become pipe-delimited."""

def table_to_markdown(table_tag: Tag) -> str:
    """Convert HTML <table> to markdown table."""

def fetch_filing_html(ticker: str, year: int, quarter: int) -> tuple[bytes, str, str]:
    """Thin wrapper: get_filings() → fetch_filing_htm() → raw HTML. See above."""

def get_filing_sections_cached(ticker, year, quarter, sections=None) -> dict:
    """Cache wrapper. Cache file: exports/{TICKER}_{Q}Q{YY}_sections.json"""
```

## Algorithm: Header Detection

1. Walk all text-containing tags in document order
2. For each tag, extract visible text, collapse whitespace
3. Test against section regex patterns (case-insensitive, flexible separators: `.`, `:`, `—`, `-`, `–`)
4. Filter out TOC links using heuristics:
   - Tag is inside `<a href="#...">` (internal link)
   - Tag is inside a `<table>` with many `#` anchor links
   - Tag's parent container has many other item-pattern matches clustered together
5. Deduplicate: if a section key appears multiple times, keep the LAST occurrence (real header comes after TOC)
6. Sort by document order

## Algorithm: Content Extraction

1. For each header, collect all following elements until the next header tag
2. Convert collected HTML to text via `html_to_text()`
3. Extract `<table>` elements separately as markdown tables
4. Compute word count per section

## Algorithm: HTML-to-Text

- `<table>` → pipe-delimited markdown table
- `<p>`, `<div>` → paragraph breaks (double newline)
- `<li>` → `"- "` prefix
- `<b>`/`<strong>` → `**bold**`
- `<h1>`-`<h6>` → `#` prefix
- Collapse 3+ consecutive newlines to 2
- Strip per-line leading/trailing whitespace

## MCP Tool Definition

```python
# In mcp_server.py — add to list_tools() and call_tool()
Tool(
    name="get_filing_sections",
    description="Parse qualitative sections from SEC 10-K or 10-Q filings. Returns narrative text (Risk Factors, MD&A, Business Description, etc.) with clean text, embedded tables, and word counts.",
    inputSchema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Stock ticker (e.g., AAPL)"},
            "year": {"type": "integer", "description": "Fiscal year"},
            "quarter": {"type": "integer", "description": "Quarter 1-4"},
            "sections": {
                "type": "array", "items": {"type": "string"},
                "description": "Optional section keys to return. If omitted, returns all."
            },
        },
        "required": ["ticker", "year", "quarter"],
    },
)
```

## Reuse from Existing Code

- `config.py`: `HEADERS`, `REQUEST_DELAY`, `EXPORT_UPDATER_DIR`
- `utils.py`: `lookup_cik_from_ticker()`
- `edgar_tools.py`:
  - `get_filings(ticker, year, quarter)` — resolves ticker → CIK → accession + filing type. **Use this directly** instead of reimplementing accession resolution.
  - `fetch_filing_htm(cik, accession)` — **NEW helper to add** (extracted from `edgar_pipeline.try_all_htm_files()`). Fetches index.json, picks largest .htm, downloads raw HTML. Shared by both the section parser and the iXBRL pipeline.
  - `_validate_ticker()` — input validation (called internally by `get_filings`)
- `edgar_pipeline.py` lines 1270-1296: the index.json → largest .htm logic to extract into `fetch_filing_htm()`. After extraction, refactor `try_all_htm_files()` to call the new shared helper.

## Edge Cases

1. Header format variations: "ITEM 1", "Item 1.", "Item 1 --", "Item 1:", "Item1." (no space), unicode dashes `\u2013`, `\u2014`
2. TOC vs actual header: TOC entries are `<a href="#item1">` inside `<table>`, real headers are later and not inside anchor tags
3. Missing sections: not all filings have every item — return `sections_missing`
4. Nested HTML: headers inside `<b><font size="4"><p>Item 7...</p></font></b>` — match on extracted text regardless of nesting
5. Item 8 (Financial Statements): very large, table-heavy — parse it but note it may be huge
6. Multi-document filings: handled by `fetch_filing_htm()` (largest-file-first with fallback)
7. Amended filings (10-K/A): skip for now, only parse originals
8. Very large filings (>10MB): BeautifulSoup will be slow but functional

## Test Strategy

**Tickers:** AAPL, MSFT, JPM (complex MD&A), TSLA (unusual formatting), BRK-B (distinctive format)

**Tests:**
1. Header detection — all expected sections found for both 10-K and 10-Q
2. TOC filtering — TOC links excluded, only real headers retained
3. Section boundaries — Item 7 text doesn't bleed into Item 7A
4. Table extraction — MD&A tables render as readable markdown
5. Cache hit/miss — first call creates JSON, second loads from cache
6. Section filtering — `sections=["item_7"]` returns only Item 7
7. Error handling — invalid ticker, future quarter, pre-2019 filing

## Verification

```bash
cd /Users/henrychien/Documents/Jupyter/edgar_updater
python -c "from section_parser import get_filing_sections_cached; print(get_filing_sections_cached('AAPL', 2024, 3, sections=['item_7']))"
```
Then test via MCP: restart edgar-financials MCP server, call `get_filing_sections(ticker="AAPL", year=2024, quarter=3, sections=["item_7"])`
