#!/usr/bin/env python3
"""
MCP Server for EDGAR Financial Data.
"""

import asyncio
import json
import os
import sys
from contextlib import redirect_stdout

from mcp.server import InitializationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import ServerCapabilities, TextContent, Tool

from edgar_tools import get_filings, get_financials, get_metric, get_filing_sections

server = Server("edgar-financials")


def _json_text(payload: dict) -> str:
    """
    Serialize tool payloads safely.

    Guardrail: if a tool accidentally returns a non-JSON-serializable object,
    return a typed error payload instead of crashing the MCP response cycle.
    """
    try:
        return json.dumps(payload, indent=2, default=str)
    except Exception as exc:
        fallback = {
            "status": "error",
            "message": "Failed to serialize MCP tool response",
            "details": str(exc),
        }
        return json.dumps(fallback, indent=2)


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="get_filings",
            description=(
                "Fetch SEC filing metadata for a company. Returns list of 10-Q, 10-K, "
                "and 8-K (earnings release) filings with URLs, dates, and fiscal period assignments."
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
            name="get_filing_sections",
            description=(
                "Parse qualitative sections from SEC 10-K or 10-Q filings. "
                "Returns narrative text (Risk Factors, MD&A, Business Description, etc.) "
                "with clean text, embedded tables, and word counts.\n\n"
                "Recommended workflow:\n"
                "1. Call with default format='summary' to see available sections and word counts.\n"
                "2. Identify the section(s) you need.\n"
                "3. Call again with format='full' and sections=['item_7'] to get text for specific sections.\n\n"
                "Defaults to summary mode (metadata only - no text content). "
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
                    "output": {
                        "type": "string",
                        "enum": ["inline", "file"],
                        "description": (
                            "Output mode. 'inline' (default) returns response content inline. "
                            "'file' writes full untruncated markdown to disk and returns metadata + file_path."
                        ),
                        "default": "inline",
                    },
                },
                "required": ["ticker", "year", "quarter"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    # MCP stdio requires stdout to contain only JSON-RPC frames.
    # The EDGAR pipeline prints extensive progress logs; route those to stderr.
    try:
        with redirect_stdout(sys.stderr):
            if name == "get_filings":
                result = get_filings(
                    ticker=arguments["ticker"],
                    year=arguments["year"],
                    quarter=arguments["quarter"],
                )
            elif name == "get_financials":
                result = get_financials(
                    ticker=arguments["ticker"],
                    year=arguments["year"],
                    quarter=arguments["quarter"],
                    full_year_mode=arguments.get("full_year_mode", False),
                    source=arguments.get("source", "auto"),
                )
            elif name == "get_metric":
                result = get_metric(
                    ticker=arguments["ticker"],
                    year=arguments["year"],
                    quarter=arguments["quarter"],
                    metric_name=arguments["metric_name"],
                    full_year_mode=arguments.get("full_year_mode", False),
                    source=arguments.get("source", "auto"),
                    date_type=arguments.get("date_type"),
                )
            elif name == "get_filing_sections":
                result = get_filing_sections(
                    ticker=arguments["ticker"],
                    year=arguments["year"],
                    quarter=arguments["quarter"],
                    sections=arguments.get("sections"),
                    format=arguments.get("format", "summary"),
                    max_words=arguments.get("max_words", 3000),
                    output=arguments.get("output", "inline"),
                )
            else:
                result = {"status": "error", "message": f"Unknown tool: {name}"}
    except Exception as exc:
        result = {
            "status": "error",
            "message": f"Unhandled error in MCP tool '{name}': {exc}",
        }

    return [TextContent(type="text", text=_json_text(result))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="edgar-financials",
                server_version="0.1.0",
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
