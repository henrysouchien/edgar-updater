#!/usr/bin/env python3
"""
MCP Server for EDGAR Financial Data.

Proxies all tools to the remote EDGAR API (financialmodelupdater.com).
No local pipeline imports — the MCP server is a pure API client.
"""

import asyncio
import json
import os
import re
import sys
import time
from contextlib import redirect_stdout
from difflib import SequenceMatcher
from pathlib import Path

import requests
from mcp.server import InitializationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import ServerCapabilities, TextContent, Tool

FILE_OUTPUT_DIR = Path(__file__).resolve().parent / "exports" / "file_output"

server = Server("edgar-financials")

# ---------------------------------------------------------------------------
# Remote API helpers
# ---------------------------------------------------------------------------

def _get_api_config():
    base_url = os.getenv("EDGAR_API_URL", "https://www.financialmodelupdater.com").rstrip("/")
    api_key = os.getenv("EDGAR_API_KEY", "")
    return base_url, api_key


def _call_api(path: str, params: dict, timeout: int = 60) -> dict:
    """HTTP GET to the remote EDGAR API. Returns parsed JSON or error dict."""
    base_url, api_key = _get_api_config()
    if not api_key:
        return {"status": "error", "message": "EDGAR_API_KEY is not configured"}

    url = f"{base_url}{path}"
    payload = dict(params)
    payload["key"] = api_key

    t0 = time.time()
    try:
        resp = requests.get(url, params=payload, timeout=timeout)
    except requests.RequestException as exc:
        return {"status": "error", "message": f"EDGAR API request failed after {time.time()-t0:.1f}s: {exc}"}

    try:
        data = resp.json()
    except ValueError:
        return {"status": "error", "message": f"Invalid JSON from EDGAR API (HTTP {resp.status_code})"}

    if resp.status_code != 200:
        if isinstance(data, dict) and data:
            return data
        return {"status": "error", "message": f"EDGAR API error (HTTP {resp.status_code})"}

    return data


def _safe_filename_part(value: str, fallback: str) -> str:
    """Normalize untrusted text into a filesystem-safe filename segment."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value).strip())
    cleaned = cleaned.strip("-_")
    return cleaned or fallback


def _pick_metric_values(fact: dict) -> tuple[object, object]:
    """Mirror /api/metric value precedence for a fact record."""
    current = fact.get("current_value")
    if current is None:
        current = fact.get("visual_current_value")
    if current is None:
        current = fact.get("current_period_value")

    prior = fact.get("prior_value")
    if prior is None:
        prior = fact.get("visual_prior_value")
    if prior is None:
        prior = fact.get("prior_period_value")

    return current, prior


def _split_identifier_tokens(value: object) -> list[str]:
    """
    Tokenize metric/tag text for fuzzy matching.
    Handles namespace separators, camel/Pascal case, and punctuation.
    """
    if value is None:
        return []

    text = str(value)
    text = text.replace(":", " ").replace("/", " ")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"[^A-Za-z0-9]+", " ", text)
    return [token for token in text.lower().split() if token]


_SEARCH_QUERY_PHRASE_ALIASES = {
    "eps": ["earnings", "per", "share"],
    "diluted eps": ["earnings", "per", "share", "diluted"],
    "basic eps": ["earnings", "per", "share", "basic"],
    "revenue": ["revenue", "from", "contract", "with", "customer"],
    "capex": ["capital", "expenditures", "payments", "to", "acquire", "property", "plant", "and", "equipment"],
    "d a": ["depreciation", "and", "amortization"],
    "da": ["depreciation", "and", "amortization"],
    "sg a": ["selling", "general", "and", "administrative"],
    "sga": ["selling", "general", "and", "administrative"],
    "r d": ["research", "and", "development"],
    "cogs": ["cost", "of", "goods", "sold", "cost", "of", "revenue"],
    "fcf": ["free", "cash", "flow"],
    "ocf": ["operating", "cash", "flow", "net", "cash", "provided", "by", "operating", "activities"],
    "ppe": ["property", "plant", "and", "equipment"],
    "goodwill": ["goodwill"],
    "shares outstanding": ["common", "stock", "shares", "outstanding"],
}

_SEARCH_QUERY_TOKEN_ALIASES = {
    "eps": ["earnings", "per", "share"],
    "rev": ["revenue"],
    "capex": ["capital", "expenditures"],
    "ocf": ["operating", "cash", "flow"],
    "fcf": ["free", "cash", "flow"],
    "cogs": ["cost", "of", "revenue"],
    "ppe": ["property", "plant", "and", "equipment"],
    "sga": ["selling", "general", "and", "administrative"],
    "da": ["depreciation", "and", "amortization"],
}


def _expand_query_variants(query: str) -> list[list[str]]:
    """Return tokenized query variants for robust matching."""
    base_tokens = _split_identifier_tokens(query)
    if not base_tokens:
        return []

    variants = {tuple(base_tokens)}

    base_phrase = " ".join(base_tokens)
    phrase_alias = _SEARCH_QUERY_PHRASE_ALIASES.get(base_phrase)
    if phrase_alias:
        variants.add(tuple(phrase_alias))

    for i, token in enumerate(base_tokens):
        replacement = _SEARCH_QUERY_TOKEN_ALIASES.get(token)
        if replacement:
            expanded = base_tokens[:i] + replacement + base_tokens[i + 1 :]
            variants.add(tuple(expanded))
            if token == "eps" and "diluted" in base_tokens:
                variants.add(tuple(["earnings", "per", "share", "diluted"]))
            if token == "eps" and "basic" in base_tokens:
                variants.add(tuple(["earnings", "per", "share", "basic"]))

    return [list(variant) for variant in variants]


def _normalize_date_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized in {"Q", "YTD", "FY"} else None


def _build_metric_catalog(financials_result: dict, date_type: str | None = None) -> list[dict]:
    """Create a deduplicated metric catalog from /api/financials facts."""
    facts = financials_result.get("facts")
    if not isinstance(facts, list):
        return []

    target_date_type = _normalize_date_type(date_type)
    catalog = {}

    for fact in facts:
        if not isinstance(fact, dict):
            continue
        raw_tag = fact.get("tag")
        if not raw_tag or not isinstance(raw_tag, str):
            continue

        fact_date_type = _normalize_date_type(fact.get("date_type"))
        if target_date_type and fact_date_type != target_date_type:
            continue

        bare_tag = raw_tag.split(":", 1)[1] if ":" in raw_tag else raw_tag
        current, prior = _pick_metric_values(fact)
        candidate = {
            "metric_name": bare_tag,
            "tag": raw_tag,
            "date_type": fact_date_type,
            "scale": fact.get("scale"),
            "current_value": current,
            "prior_value": prior,
            "has_value": current is not None or prior is not None,
        }

        key = (raw_tag.lower(), fact_date_type or "")
        existing = catalog.get(key)
        if existing is None:
            catalog[key] = candidate
            continue

        # Prefer entries with usable values.
        if candidate["has_value"] and not existing["has_value"]:
            catalog[key] = candidate

    return sorted(catalog.values(), key=lambda item: ((item["metric_name"] or "").lower(), item["date_type"] or ""))


def _score_metric_match(query: str, metric: dict) -> float:
    query_variants = _expand_query_variants(query)
    if not query_variants:
        return 0.0

    metric_tokens = _split_identifier_tokens(metric.get("metric_name", ""))
    metric_tokens += _split_identifier_tokens(metric.get("tag", ""))
    date_type = _normalize_date_type(metric.get("date_type"))
    if date_type:
        metric_tokens.append(date_type.lower())
    if not metric_tokens:
        return 0.0

    metric_text = " ".join(metric_tokens)
    metric_compact = "".join(metric_tokens)
    metric_token_set = set(metric_tokens)

    best_score = 0.0
    for query_tokens in query_variants:
        query_text = " ".join(query_tokens)
        query_compact = "".join(query_tokens)

        if query_text == metric_text or query_compact == metric_compact:
            best_score = max(best_score, 100.0)
            continue

        # Phrase and compact containment handle spaces/hyphens/camel-case differences.
        if query_text and query_text in metric_text:
            best_score = max(best_score, 94.0)
        if query_compact and query_compact in metric_compact:
            best_score = max(best_score, 90.0)

        # Token coverage captures multi-word fuzzy matches.
        overlap = sum(1 for token in query_tokens if token in metric_token_set)
        if overlap:
            coverage = overlap / max(len(query_tokens), 1)
            best_score = max(best_score, 55.0 + (coverage * 30.0))

        # Final safety net for near matches.
        if query_compact and metric_compact:
            ratio = SequenceMatcher(None, query_compact, metric_compact).ratio()
            if ratio >= 0.55:
                best_score = max(best_score, 45.0 + (ratio * 35.0))

    return round(best_score, 2)


def _deadline_expired(args: dict) -> bool:
    """Cooperative timeout check for worker-thread handlers."""
    deadline = args.get("__deadline_monotonic")
    if deadline is None:
        return False
    try:
        return time.monotonic() >= float(deadline)
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Tool dispatch — remote API proxies
# ---------------------------------------------------------------------------

def _proxy_get_filings(args: dict) -> dict:
    return _call_api("/api/filings", {
        "ticker": args["ticker"],
        "year": args["year"],
        "quarter": args["quarter"],
    })


def _proxy_get_financials(args: dict) -> dict:
    output_mode = args.get("output", "file")

    result = _call_api("/api/financials", {
        "ticker": args["ticker"],
        "year": args["year"],
        "quarter": args["quarter"],
        "full_year_mode": str(args.get("full_year_mode", False)).lower(),
        "source": args.get("source", "auto"),
    })

    if result.get("status") != "success" or output_mode != "file":
        return result

    # Write full JSON to local file, return summary + file_path
    ticker = _safe_filename_part(str(args["ticker"]).upper(), "ticker")
    year = int(args["year"])
    quarter = int(args["quarter"])
    source_info = (result.get("metadata", {}).get("source") or result.get("source") or {})
    filing_type = source_info.get("filing_type", "")

    FILE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{ticker}_{quarter}Q{year % 100:02d}_financials.json"
    file_path = (FILE_OUTPUT_DIR / filename).resolve()
    root_dir = FILE_OUTPUT_DIR.resolve()
    if not file_path.is_relative_to(root_dir):
        return {"status": "error", "message": "Invalid output path"}
    if _deadline_expired(args):
        return {"status": "error", "message": "Request timed out before file output could be written"}

    file_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    facts = result.get("facts", []) if isinstance(result.get("facts"), list) else []

    return {
        "status": "success",
        "ticker": ticker,
        "year": year,
        "quarter": quarter,
        "filing_type": filing_type,
        "output": "file",
        "file_path": str(file_path.resolve()),
        "hint": "Use Read tool with file_path. Use jq or Grep to search for specific metrics.",
        "metadata": {
            "total_facts": len(facts),
            "source": source_info,
        },
    }


def _proxy_get_metric(args: dict) -> dict:
    return _call_api("/api/metric", {
        "ticker": args["ticker"],
        "year": args["year"],
        "quarter": args["quarter"],
        "metric_name": args["metric_name"],
        "full_year_mode": str(args.get("full_year_mode", False)).lower(),
        "source": args.get("source", "auto"),
        "date_type": args.get("date_type", ""),
    })


def _proxy_list_metrics(args: dict) -> dict:
    date_type = _normalize_date_type(args.get("date_type"))
    limit = args.get("limit", 200)
    include_values = bool(args.get("include_values", True))
    try:
        limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        return {"status": "error", "message": "limit must be an integer between 1 and 1000"}

    financials = _call_api("/api/financials", {
        "ticker": args["ticker"],
        "year": args["year"],
        "quarter": args["quarter"],
        "full_year_mode": str(args.get("full_year_mode", False)).lower(),
        "source": args.get("source", "auto"),
    })
    if financials.get("status") != "success":
        return financials

    catalog = _build_metric_catalog(financials, date_type=date_type)
    total_candidates = len(catalog)
    catalog = catalog[:limit]

    if not include_values:
        for item in catalog:
            item.pop("current_value", None)
            item.pop("prior_value", None)

    metadata = financials.get("metadata", {}) if isinstance(financials.get("metadata"), dict) else {}
    return {
        "status": "success",
        "ticker": str(args["ticker"]).upper(),
        "year": int(args["year"]),
        "quarter": int(args["quarter"]),
        "full_year_mode": bool(args.get("full_year_mode", False)),
        "source": metadata.get("source", {}),
        "date_type_filter": date_type,
        "total_candidates": total_candidates,
        "returned_candidates": len(catalog),
        "metrics": catalog,
        "hint": "Pass metric_name from this list into get_metric.",
    }


def _proxy_search_metrics(args: dict) -> dict:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"status": "error", "message": "Missing required parameter: query"}

    date_type = _normalize_date_type(args.get("date_type"))
    limit = args.get("limit", 20)
    include_values = bool(args.get("include_values", True))
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        return {"status": "error", "message": "limit must be an integer between 1 and 100"}

    financials = _call_api("/api/financials", {
        "ticker": args["ticker"],
        "year": args["year"],
        "quarter": args["quarter"],
        "full_year_mode": str(args.get("full_year_mode", False)).lower(),
        "source": args.get("source", "auto"),
    })
    if financials.get("status") != "success":
        return financials

    catalog = _build_metric_catalog(financials, date_type=date_type)
    ranked = []
    for item in catalog:
        score = _score_metric_match(query, item)
        if score <= 0:
            continue
        ranked.append({**item, "match_score": round(score, 2)})

    ranked.sort(key=lambda item: (-item["match_score"], item["metric_name"].lower(), item.get("date_type") or ""))
    ranked = ranked[:limit]

    if not include_values:
        for item in ranked:
            item.pop("current_value", None)
            item.pop("prior_value", None)

    metadata = financials.get("metadata", {}) if isinstance(financials.get("metadata"), dict) else {}
    return {
        "status": "success",
        "ticker": str(args["ticker"]).upper(),
        "year": int(args["year"]),
        "quarter": int(args["quarter"]),
        "full_year_mode": bool(args.get("full_year_mode", False)),
        "query": query,
        "date_type_filter": date_type,
        "source": metadata.get("source", {}),
        "total_matches": len(ranked),
        "matches": ranked,
        "hint": "Use top match.metric_name with get_metric, then validate returned metric tag/value.",
    }


def _proxy_get_filing_sections(args: dict) -> dict:
    """Proxy filing sections to remote API, with local file-write for output='file'."""
    output_mode = args.get("output", "file")
    sections_list = args.get("sections")
    tables_only = args.get("tables_only", False)

    # Build remote API params
    params = {
        "ticker": args["ticker"],
        "year": args["year"],
        "quarter": args["quarter"],
        "format": args.get("format", "summary"),
    }
    if sections_list:
        params["sections"] = ",".join(sections_list)

    if output_mode == "file":
        # Fetch full untruncated text for file output
        params["format"] = "full"
        params["max_words"] = "none"
    else:
        max_words = args.get("max_words", 3000)
        params["max_words"] = str(max_words) if max_words is not None else "none"

    result = _call_api("/api/sections", params)

    if result.get("status") != "success":
        return result

    # Normalize section-level table counts for both inline and file modes.
    for section in result.get("sections", {}).values():
        if not isinstance(section, dict):
            continue
        tables = section.get("tables", []) or []
        nonempty_tables = [t for t in tables if (t or "").strip()]
        section["table_count"] = len(nonempty_tables)
    total_tables = sum(
        s.get("table_count", 0)
        for s in result.get("sections", {}).values()
        if isinstance(s, dict)
    )
    if not isinstance(result.get("metadata"), dict):
        result["metadata"] = {}
    result["metadata"]["total_table_count"] = total_tables

    # Strip narrative text if tables_only requested
    if tables_only:
        for section in result.get("sections", {}).values():
            if not isinstance(section, dict):
                continue
            section.pop("text", None)
            table_words = 0
            for table in section.get("tables", []) or []:
                table_text = (table or "").strip()
                if table_text:
                    table_words += len(table_text.split())
            section["word_count"] = table_words

    if output_mode != "file":
        return result

    # Write sections to local markdown file
    ticker = _safe_filename_part(str(args["ticker"]).upper(), "ticker")
    year = int(args["year"])
    quarter = int(args["quarter"])
    filing_type = result.get("filing_type", "")
    sections_data = result.get("sections", {})

    FILE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build filename
    if sections_list:
        safe_keys = [_safe_filename_part(key, "section") for key in sorted(sections_list)]
        keys_part = "_".join(safe_keys)
        filename = f"{ticker}_{quarter}Q{year % 100:02d}_{keys_part}.md"
    else:
        filename = f"{ticker}_{quarter}Q{year % 100:02d}_sections.md"
    file_path = (FILE_OUTPUT_DIR / filename).resolve()
    root_dir = FILE_OUTPUT_DIR.resolve()
    if not file_path.is_relative_to(root_dir):
        return {"status": "error", "message": "Invalid output path"}
    if _deadline_expired(args):
        return {"status": "error", "message": "Request timed out before file output could be written"}

    # Build markdown
    total_words = sum(s.get("word_count", 0) for s in sections_data.values())
    section_keys = ", ".join(sections_data.keys()) if sections_data else "none"
    lines = [
        f"# {ticker} {filing_type} - Q{quarter} FY{year}: Filing Sections",
        f"> Sections: {section_keys} | Total words: {total_words:,} | Total tables: {total_tables:,}",
        "---",
    ]
    for section in sections_data.values():
        header = section.get("header", "Unknown Section")
        lines.append(f"## SECTION: {header}")
        lines.append(f"**Word count:** {section.get('word_count', 0):,}")
        lines.append(f"**Table count:** {section.get('table_count', 0):,}")
        if not tables_only:
            text = section.get("text", "").strip()
            if text:
                lines.append(text)
        tables = section.get("tables", [])
        if tables:
            lines.append("### TABLES")
            for table in tables:
                table_text = (table or "").strip()
                if table_text:
                    lines.append(table_text)
        lines.append("---")
    if lines and lines[-1] == "---":
        lines.pop()

    file_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    # Return summary (no full text inline) + file_path
    summary_sections = {
        key: {
            "header": s.get("header"),
            "word_count": s.get("word_count", 0),
            "table_count": s.get("table_count", 0),
        }
        for key, s in sections_data.items()
    }
    return {
        "status": "success",
        "ticker": ticker,
        "year": year,
        "quarter": quarter,
        "filing_type": filing_type,
        "output": "file",
        "file_path": str(file_path.resolve()),
        "hint": "Use Read tool with file_path. Grep '^## SECTION:' for anchors.",
        "sections": summary_sections,
        "sections_found": list(sections_data.keys()),
        "metadata": {
            "total_word_count": total_words,
            "total_words": total_words,
            "total_table_count": total_tables,
            "section_count": len(sections_data),
        },
    }


# ---------------------------------------------------------------------------
# JSON serializer
# ---------------------------------------------------------------------------

def _json_text(payload: dict) -> str:
    try:
        return json.dumps(payload, indent=2, default=str)
    except Exception as exc:
        fallback = {
            "status": "error",
            "message": "Failed to serialize MCP tool response",
            "details": str(exc),
        }
        return json.dumps(fallback, indent=2)


# ---------------------------------------------------------------------------
# MCP tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="get_filings",
            description=(
                "Fetch SEC filing metadata for a company. Returns list of 10-Q, 10-K, "
                "and 8-K (earnings release) filings with URLs, dates, and fiscal period assignments. "
                "Share filing URLs with the user for reference, but do NOT attempt to fetch them "
                "yourself via WebFetch — SEC blocks automated requests. To read filing content, "
                "use get_filing_sections (for parsed narrative/tables) or get_financials/get_metric "
                "(for structured XBRL data)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker symbol (e.g., MSCI, AAPL)",
                    },
                    "year": {"type": "integer", "description": "Fiscal year (e.g., 2025)"},
                    "quarter": {"type": "integer", "description": "Quarter 1-4"},
                },
                "required": ["ticker", "year", "quarter"],
            },
        ),
        Tool(
            name="get_financials",
            description=(
                "Extract all financial facts from SEC filings. Returns structured JSON with income "
                "statement, balance sheet, and cash flow data. Each fact includes a 'scale' field "
                "(e.g., 'millions', 'thousands', 'units') indicating the unit scale of the values."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol"},
                    "year": {"type": "integer", "description": "Fiscal year"},
                    "quarter": {"type": "integer", "description": "Quarter 1-4"},
                    "full_year_mode": {
                        "type": "boolean",
                        "description": "If true, return full-year (10-K) data instead of quarterly",
                        "default": False,
                    },
                    "source": {
                        "type": "string",
                        "enum": ["auto", "8k"],
                        "default": "auto",
                        "description": "Data source. 'auto' = try 10-Q/10-K first, fall back to 8-K. '8k' = 8-K earnings release only.",
                    },
                    "output": {
                        "type": "string",
                        "enum": ["inline", "file"],
                        "default": "file",
                        "description": (
                            "Output mode. 'file' (default) writes full JSON to disk and returns summary + file_path. "
                            "'inline' returns full JSON response inline (may exceed token limits)."
                        ),
                    },
                },
                "required": ["ticker", "year", "quarter"],
            },
        ),
        Tool(
            name="get_metric",
            description=(
                "Get a specific financial metric. Supports common names like 'revenue', "
                "'net_income', 'eps', 'gross_profit', 'operating_income', 'cash', 'total_assets', "
                "'total_debt'. Returns current/prior values with YoY change. Includes 'scale' "
                "field -- multiply displayed value by scale to get actual dollars "
                "(e.g., revenue=6800, scale='millions' means $6.8B)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol"},
                    "year": {"type": "integer", "description": "Fiscal year"},
                    "quarter": {"type": "integer", "description": "Quarter 1-4"},
                    "metric_name": {
                        "type": "string",
                        "description": "Metric name or XBRL tag (e.g., 'revenue', 'NetIncomeLoss')",
                    },
                    "full_year_mode": {
                        "type": "boolean",
                        "description": "If true, return full-year data",
                        "default": False,
                    },
                    "source": {
                        "type": "string",
                        "enum": ["auto", "8k"],
                        "default": "auto",
                        "description": "Data source. 'auto' = try 10-Q/10-K first, fall back to 8-K. '8k' = 8-K earnings release only.",
                    },
                    "date_type": {
                        "type": "string",
                        "enum": ["Q", "YTD", "FY"],
                        "description": "Filter by period type. 'Q' = quarterly, 'YTD' = year-to-date, 'FY' = full-year/annual. If omitted, inferred from full_year_mode.",
                    },
                },
                "required": ["ticker", "year", "quarter", "metric_name"],
            },
        ),
        Tool(
            name="list_metrics",
            description=(
                "List available metric tags for a filing period so an agent can choose an exact "
                "metric_name before calling get_metric."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol"},
                    "year": {"type": "integer", "description": "Fiscal year"},
                    "quarter": {"type": "integer", "description": "Quarter 1-4"},
                    "full_year_mode": {
                        "type": "boolean",
                        "description": "If true, use annual/full-year context",
                        "default": False,
                    },
                    "source": {
                        "type": "string",
                        "enum": ["auto", "8k"],
                        "default": "auto",
                        "description": "Data source. 'auto' = try 10-Q/10-K first, fall back to 8-K. '8k' = 8-K only.",
                    },
                    "date_type": {
                        "type": "string",
                        "enum": ["Q", "YTD", "FY"],
                        "description": "Optional period filter for listed metrics.",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 200,
                        "description": "Max number of metrics to return (1-1000).",
                    },
                    "include_values": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include current/prior values in each listed metric candidate.",
                    },
                },
                "required": ["ticker", "year", "quarter"],
            },
        ),
        Tool(
            name="search_metrics",
            description=(
                "Search available filing metrics by natural-language query (e.g., 'diluted eps', "
                "'total liabilities', 'operating cash flow') and return ranked candidates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol"},
                    "year": {"type": "integer", "description": "Fiscal year"},
                    "quarter": {"type": "integer", "description": "Quarter 1-4"},
                    "query": {"type": "string", "description": "Search query for metric discovery."},
                    "full_year_mode": {
                        "type": "boolean",
                        "description": "If true, use annual/full-year context",
                        "default": False,
                    },
                    "source": {
                        "type": "string",
                        "enum": ["auto", "8k"],
                        "default": "auto",
                        "description": "Data source. 'auto' = try 10-Q/10-K first, fall back to 8-K. '8k' = 8-K only.",
                    },
                    "date_type": {
                        "type": "string",
                        "enum": ["Q", "YTD", "FY"],
                        "description": "Optional period filter for search results.",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 20,
                        "description": "Max ranked matches to return (1-100).",
                    },
                    "include_values": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include current/prior values in each match.",
                    },
                },
                "required": ["ticker", "year", "quarter", "query"],
            },
        ),
        Tool(
            name="get_filing_sections",
            description=(
                "Parse qualitative sections from SEC 10-K or 10-Q filings. "
                "Returns narrative text (Risk Factors, MD&A, Business Description, etc.) "
                "with clean text, embedded tables, and word counts.\n\n"
                "Default behavior writes to file (output='file'). For lightweight discovery, "
                "call with output='inline' and format='summary'.\n\n"
                "Recommended workflow:\n"
                "1. Call with output='inline', format='summary' to see available sections and word counts.\n"
                "2. Identify the section(s) you need.\n"
                "3. Call again with output='file' and sections=['item_7'] for full untruncated export.\n\n"
                "Inline mode defaults to summary (metadata only - no text content). "
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
                            "section names, word counts, filing type - no text content. "
                            "'full' returns section text, subject to max_words truncation."
                        ),
                    },
                    "max_words": {
                        "type": ["integer", "null"],
                        "description": (
                            "Max words per section text field (default 3000). "
                            "Only applies when format='full'. "
                            "Set to null for unlimited (use with caution - large sections can exceed 10K words)."
                        ),
                    },
                    "tables_only": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "If true, strip narrative text and return only markdown tables from each section. "
                            "Use when you only need financial data tables, not the surrounding discussion."
                        ),
                    },
                    "output": {
                        "type": "string",
                        "enum": ["inline", "file"],
                        "description": (
                            "Output mode. 'file' (default) writes full untruncated markdown to disk and returns metadata + file_path. "
                            "'inline' returns response content inline (may exceed token limits)."
                        ),
                        "default": "file",
                    },
                },
                "required": ["ticker", "year", "quarter"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# MCP tool handler
# ---------------------------------------------------------------------------

_TOOL_DISPATCH = {
    "get_filings": _proxy_get_filings,
    "get_financials": _proxy_get_financials,
    "get_metric": _proxy_get_metric,
    "list_metrics": _proxy_list_metrics,
    "search_metrics": _proxy_search_metrics,
    "get_filing_sections": _proxy_get_filing_sections,
}

_TOOL_TIMEOUT = {
    "get_filings": 30,
    "get_financials": 60,
    "get_metric": 30,
    "list_metrics": 45,
    "search_metrics": 45,
    "get_filing_sections": 60,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    handler = _TOOL_DISPATCH.get(name)
    if not handler:
        result = {"status": "error", "message": f"Unknown tool: {name}"}
        return [TextContent(type="text", text=_json_text(result))]

    timeout = _TOOL_TIMEOUT.get(name, 60)
    call_args = dict(arguments or {})
    call_args["__deadline_monotonic"] = time.monotonic() + timeout
    try:
        with redirect_stdout(sys.stderr):
            result = await asyncio.wait_for(
                asyncio.to_thread(handler, call_args),
                timeout=timeout,
            )
    except asyncio.TimeoutError:
        result = {"status": "error", "message": f"Tool '{name}' timed out after {timeout}s"}
    except Exception as exc:
        result = {"status": "error", "message": f"Unhandled error in MCP tool '{name}': {exc}"}

    return [TextContent(type="text", text=_json_text(result))]


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

async def main():
    # Validate API key on startup
    _, api_key = _get_api_config()
    if not api_key:
        print("WARNING: EDGAR_API_KEY not set — remote API tools will fail", file=sys.stderr)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="edgar-financials",
                server_version="0.2.0",
                capabilities=ServerCapabilities(tools={}),
            ),
        )


def _kill_previous_instance():
    """Kill any previous edgar MCP server instance spawned by the same parent session."""
    import signal
    from pathlib import Path
    server_dir = Path(__file__).resolve().parent
    ppid = os.getppid()
    pid_file = server_dir / f".edgar_mcp_server_{ppid}.pid"
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            if old_pid != os.getpid():
                os.kill(old_pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    pid_file.write_text(str(os.getpid()))
    # Clean up stale PID files from dead sessions
    for stale in server_dir.glob(".edgar_mcp_server_*.pid"):
        if stale == pid_file:
            continue
        try:
            session_pid = int(stale.stem.split("_")[-1])
            os.kill(session_pid, 0)  # check if parent session is alive
        except (ValueError, ProcessLookupError):
            stale.unlink(missing_ok=True)
        except PermissionError:
            pass  # process exists but owned by another user


if __name__ == "__main__":
    _kill_previous_instance()
    asyncio.run(main())
