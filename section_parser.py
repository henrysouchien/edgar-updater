import copy
import json
import os
import re
import time

from bs4 import BeautifulSoup, Tag

from config import HEADERS, REQUEST_DELAY, EXPORT_UPDATER_DIR
from edgar_tools import get_filings, fetch_filing_htm

_SEP = r"[\.\:\s\u2013\u2014\-]+"


def _flex(word: str) -> str:
    """Allow optional spaces within a keyword for anti-scraping resilience.

    Many SEC filings insert spaces within words to thwart scraping:
    'B USINESS', 'RIS K', 'QUANTITAT IVE', 'STATE MENTS'.
    This converts 'Business' -> 'B\\s*u\\s*s\\s*i\\s*n\\s*e\\s*s\\s*s'.
    """
    return r"\s*".join(word)


SECTIONS_10K = {
    "item_1": rf"Item\s*1{_SEP}\s*{_flex('Business')}",
    "item_1a": rf"Item\s*1A{_SEP}\s*{_flex('Risk')}\s+{_flex('Factors')}",
    "item_1b": rf"Item\s*1B{_SEP}\s*{_flex('Unresolved')}\s+{_flex('Staff')}\s+{_flex('Comments')}",
    "item_2": rf"Item\s*2{_SEP}\s*{_flex('Properties')}",
    "item_3": rf"Item\s*3{_SEP}\s*{_flex('Legal')}\s+{_flex('Proceedings')}",
    "item_7": rf"Item\s*7{_SEP}\s*{_flex('Management')}.{{0,10}}{_flex('Discussion')}",
    "item_7a": rf"Item\s*7A{_SEP}\s*{_flex('Quantitative')}\s+.*{_flex('Qualitative')}",
    "item_8": rf"Item\s*8{_SEP}\s*{_flex('Financial')}\s+{_flex('Statements')}",
}

SECTIONS_10Q = {
    "part1_item1": rf"(?:Part\s*I\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*1{_SEP}\s*{_flex('Financial')}\s+{_flex('Statements')}",
    "part1_item2": rf"(?:Part\s*I\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*2{_SEP}\s*{_flex('Management')}.{{0,10}}{_flex('Discussion')}",
    "part1_item3": rf"(?:Part\s*I\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*3{_SEP}\s*{_flex('Quantitative')}",
    "part1_item4": rf"(?:Part\s*I\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*4{_SEP}\s*{_flex('Controls')}\s+.*{_flex('Procedures')}",
    "part2_item1": rf"(?:Part\s*II\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*1{_SEP}\s*{_flex('Legal')}\s+{_flex('Proceedings')}",
    "part2_item1a": rf"(?:Part\s*II\s*[\.\:\-\u2013\u2014]*\s*)?Item\s*1A{_SEP}\s*{_flex('Risk')}\s+{_flex('Factors')}",
}

SECTION_ORDER_10K = ["item_1", "item_1a", "item_1b", "item_2", "item_3", "item_7", "item_7a", "item_8"]
SECTION_ORDER_10Q = ["part1_item1", "part1_item2", "part1_item3", "part1_item4", "part2_item1", "part2_item1a"]

_BODY_REF_PREFIXES = (
    "see ",
    "refer to ",
    "as described in ",
    "discussed in ",
    "pursuant to ",
)


def fetch_filing_html(ticker: str, year: int, quarter: int) -> tuple[bytes, str, str]:
    """
    Fetch filing HTML using the existing pipeline.

    Returns: (html_bytes, filing_type, htm_url)

    Raises:
        ValueError: if no 10-K/10-Q filing found for the given period.
    """
    result = get_filings(ticker, year, quarter)
    if result.get("status") != "success" or not result.get("filings"):
        raise ValueError(f"No filing found for {ticker} {quarter}Q{year}")

    filing = next((f for f in result["filings"] if f.get("form") in ("10-K", "10-Q")), None)
    if not filing:
        raise ValueError(f"No 10-K/10-Q found for {ticker} {quarter}Q{year}")

    accession = filing.get("accession", "")
    if not accession or "-" not in accession:
        raise ValueError(f"Invalid accession format: {accession}")

    # Extract company CIK from filing URL (accession prefix is the filing
    # agent's CIK, not the company's — e.g. 0000950170 for Donnelley).
    cik = None
    filing_url = filing.get("url", "")
    cik_match = re.search(r"/edgar/data/(\d+)/", filing_url)
    if cik_match:
        cik = cik_match.group(1)
    if not cik:
        from utils import lookup_cik_from_ticker
        cik = lookup_cik_from_ticker(ticker).lstrip("0") or accession.split("-")[0]

    html_bytes, htm_url = fetch_filing_htm(cik, accession)
    return html_bytes, filing["form"], htm_url


def parse_filing_sections(html_content: bytes | str, filing_type: str) -> dict:
    """
    Parse an SEC filing HTML document into named sections.
    """
    if filing_type not in ("10-K", "10-Q"):
        raise ValueError(f"Unsupported filing type: {filing_type}")

    soup = BeautifulSoup(html_content, "lxml")
    headers = find_section_headers(soup, filing_type)
    section_order = SECTION_ORDER_10K if filing_type == "10-K" else SECTION_ORDER_10Q
    sections = extract_section_content(soup, headers, section_order)

    sections_found = [key for key in section_order if key in sections]
    sections_missing = [key for key in section_order if key not in sections]
    total_word_count = sum(section.get("word_count", 0) for section in sections.values())

    return {
        "filing_type": filing_type,
        "sections_found": sections_found,
        "sections_missing": sections_missing,
        "sections": sections,
        "metadata": {
            "total_word_count": total_word_count,
            "section_count": len(sections),
        },
    }


def find_section_headers(soup: BeautifulSoup, filing_type: str) -> list[dict]:
    """
    Find section header elements in the parsed HTML document.

    Returns a list of dicts, each with:
        - "key": section key (e.g., "item_7")
        - "element": the BeautifulSoup Tag
        - "header_text": the matched text
        - "position": document order index
    """
    patterns = SECTIONS_10K if filing_type == "10-K" else SECTIONS_10Q
    compiled = {key: re.compile(pattern, re.IGNORECASE) for key, pattern in patterns.items()}

    raw_matches = []
    checked_blocks: set[int] = set()

    for position, text_node in enumerate(soup.find_all(string=True)):
        tag = text_node.parent
        if not isinstance(tag, Tag):
            continue
        if tag.name in ("script", "style", "noscript"):
            continue

        # Check the parent block element's full text to handle headers
        # split across multiple <span> siblings (common in MSFT, JPM, etc.)
        block = tag.find_parent(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6"]) or tag
        block_id = id(block)
        if block_id in checked_blocks:
            continue
        checked_blocks.add(block_id)

        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True)).strip()
        if not text:
            continue

        if len(text.split()) > 15:
            continue

        text_lower = text.lower()
        if text_lower.startswith(_BODY_REF_PREFIXES):
            continue

        anchor_parent = block if block.name == "a" else block.find_parent("a", href=re.compile(r"^#"))
        if anchor_parent is not None:
            href = anchor_parent.get("href", "")
            if isinstance(href, str) and href.startswith("#"):
                continue

        table_parent = block if block.name == "table" else block.find_parent("table")
        if table_parent is not None:
            anchor_links = table_parent.find_all("a", href=re.compile(r"^#"))
            if len(anchor_links) > 5:
                continue

        # Use the block itself as the container unit. Since we already
        # check at block level, TOC entries are caught by table/anchor
        # filters; the clustering guard here is defense-in-depth.
        container = block
        for key, pattern in compiled.items():
            if pattern.search(text):
                raw_matches.append(
                    {
                        "key": key,
                        "element": block,
                        "header_text": text,
                        "position": position,
                        "container_id": id(container),
                    }
                )
                break

    container_counts = {}
    for match in raw_matches:
        cid = match["container_id"]
        container_counts[cid] = container_counts.get(cid, 0) + 1

    filtered = [m for m in raw_matches if container_counts.get(m["container_id"], 0) < 3]
    filtered.sort(key=lambda x: x["position"])

    deduped = []
    seen_keys = set()
    for match in filtered:
        key = match["key"]
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(
            {
                "key": key,
                "element": match["element"],
                "header_text": match["header_text"],
                "position": match["position"],
            }
        )

    deduped.sort(key=lambda x: x["position"])
    return deduped


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
    all_tags = [node for node in soup.descendants if isinstance(node, Tag)]
    tag_positions = {id(tag): idx for idx, tag in enumerate(all_tags)}

    positioned_headers = []
    for header in headers:
        idx = tag_positions.get(id(header["element"]))
        if idx is None:
            continue
        positioned_headers.append({**header, "tag_index": idx})

    if not positioned_headers:
        return {}

    positioned_headers.sort(key=lambda h: h["tag_index"])
    section_data = {}
    for i, header in enumerate(positioned_headers):
        start_idx = header["tag_index"] + 1
        end_idx = positioned_headers[i + 1]["tag_index"] if i + 1 < len(positioned_headers) else len(all_tags)
        slice_tags = all_tags[start_idx:end_idx]

        top_level_tags = []
        included_ids = set(id(tag) for tag in slice_tags)
        for tag in slice_tags:
            parent = tag.parent if isinstance(tag.parent, Tag) else None
            if parent is not None and id(parent) in included_ids:
                continue
            top_level_tags.append(tag)

        tables = []
        seen_table_ids = set()
        for tag in top_level_tags:
            if tag.name == "table":
                markdown = table_to_markdown(tag)
                if markdown:
                    tables.append(markdown)
                seen_table_ids.add(id(tag))
                continue
            for table in tag.find_all("table"):
                table_id = id(table)
                if table_id in seen_table_ids:
                    continue
                markdown = table_to_markdown(table)
                if markdown:
                    tables.append(markdown)
                seen_table_ids.add(table_id)

        text = _html_to_text(top_level_tags, include_tables=False)
        word_count = len(text.split())
        section_data[header["key"]] = {
            "header": header["header_text"],
            "text": text,
            "tables": tables,
            "word_count": word_count,
        }

    ordered = {}
    for key in section_order:
        if key in section_data:
            ordered[key] = section_data[key]
    return ordered


def html_to_text(element) -> str:
    """
    Convert an HTML element (or list of elements) to clean markdown-like text.
    """
    return _html_to_text(element, include_tables=True)


def _html_to_text(element, include_tables: bool) -> str:
    if isinstance(element, list):
        raw = "".join(_render_node(node, include_tables) for node in element)
    else:
        raw = _render_node(element, include_tables)

    lines = [line.strip() for line in raw.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _render_node(node, include_tables: bool) -> str:
    if not isinstance(node, Tag):
        return re.sub(r"\s+", " ", str(node))

    name = (node.name or "").lower()
    if name in ("script", "style", "noscript"):
        return ""
    if name == "table":
        if not include_tables:
            return ""
        table_md = table_to_markdown(node)
        return f"{table_md}\n\n" if table_md else ""
    if name == "br":
        return "\n"

    child_text = "".join(_render_node(child, include_tables) for child in node.children).strip()
    if name == "li":
        return f"- {child_text}\n" if child_text else ""
    if name in ("b", "strong"):
        return f"**{child_text}**" if child_text else ""
    if name in ("p", "div"):
        return f"{child_text}\n\n" if child_text else ""
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(name[1])
        return f"{'#' * level} {child_text}\n\n" if child_text else ""
    return child_text


def table_to_markdown(table_tag: Tag) -> str:
    """
    Convert an HTML <table> tag to a markdown-formatted table string.
    """
    rows = []
    for row in table_tag.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        values = [" ".join(cell.stripped_strings) for cell in cells]
        if not any(values):
            continue
        rows.append(values)

    if not rows:
        return ""

    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]

    header = normalized[0]
    lines = [f"| {' | '.join(header)} |", f"| {' | '.join(['---'] * width)} |"]
    for row in normalized[1:]:
        lines.append(f"| {' | '.join(row)} |")
    return "\n".join(lines)


def _truncate(text: str, max_words: int | None) -> str:
    """Truncate text to max_words, appending continuation marker."""
    if max_words is None:
        return text
    words = text.split()
    if len(words) <= max_words:
        return text
    remaining = len(words) - max_words
    return " ".join(words[:max_words]) + f"\n\n...[truncated — {remaining:,} more words remaining]"


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
    """
    os.makedirs(EXPORT_UPDATER_DIR, exist_ok=True)
    cache_path = os.path.join(
        EXPORT_UPDATER_DIR,
        f"{ticker.upper()}_{quarter}Q{str(year)[-2:]}_sections.json",
    )

    if os.path.exists(cache_path):
        with open(cache_path, "r") as f:
            cached_result = json.load(f)
    else:
        html_bytes, filing_type, _htm_url = fetch_filing_html(ticker, year, quarter)
        cached_result = parse_filing_sections(html_bytes, filing_type)
        with open(cache_path, "w") as f:
            json.dump(cached_result, f)

    result = copy.deepcopy(cached_result)
    all_sections = result.get("sections", {})

    if sections is not None:
        requested = [key for key in sections]
        filtered_sections = {key: all_sections[key] for key in requested if key in all_sections}
        result["sections"] = filtered_sections
        result["sections_found"] = list(filtered_sections.keys())
        result["sections_missing"] = [key for key in requested if key not in filtered_sections]
    else:
        result["sections"] = all_sections
        filing_type = result.get("filing_type")
        expected = SECTION_ORDER_10K if filing_type == "10-K" else SECTION_ORDER_10Q
        result["sections_found"] = [key for key in expected if key in all_sections]
        result["sections_missing"] = [key for key in expected if key not in all_sections]

    result["metadata"] = {
        "total_word_count": sum(section.get("word_count", 0) for section in result["sections"].values()),
        "section_count": len(result["sections"]),
    }

    if format == "summary":
        summary_sections = {}
        for key, section in result["sections"].items():
            summary_sections[key] = {
                "header": section.get("header"),
                "word_count": section.get("word_count", 0),
            }
        result["sections"] = summary_sections
        result["hint"] = "Use format='full' with sections=['item_7'] to get text for a specific section."
        return result

    if format != "full":
        raise ValueError("format must be 'summary' or 'full'")

    if sections is None:
        for section in result["sections"].values():
            section["text"] = _truncate(section.get("text", ""), 500)
        result["hint"] = "Specify sections=['item_7'] to get full text for a specific section."
        return result

    for section in result["sections"].values():
        section["text"] = _truncate(section.get("text", ""), max_words)
    return result
