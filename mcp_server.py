#!/usr/bin/env python3
"""
MCP Server for EDGAR Financial Data.
"""

import asyncio
import json

from mcp.server import InitializationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import ServerCapabilities, TextContent, Tool

from edgar_tools import get_filings, get_financials, get_metric

server = Server("edgar-financials")


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
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
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
    else:
        result = {"status": "error", "message": f"Unknown tool: {name}"}

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


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


if __name__ == "__main__":
    asyncio.run(main())
