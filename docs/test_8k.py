#!/usr/bin/env python3
"""
CLI test commands for the 8-K earnings extraction pipeline.

Usage:
    python3 test_8k.py step1 MSCI 2025 4      # 8-K discovery
    python3 test_8k.py step2 MSCI 2025 4      # Claude extraction
    python3 test_8k.py step3 MSCI 2025 4      # get_financials_from_8k + get_metric
    python3 test_8k.py step4 MSCI 2025 4      # Integration: get_financials/get_metric/get_filings
    python3 test_8k.py all   MSCI 2025 4      # Run all steps

API key is loaded from .env file (via load_dotenv in config.py).
"""

import sys
import json
import os


def _fmt_val(val):
    """Format a numeric value for display."""
    if val is None:
        return f"{'null':>12}"
    if isinstance(val, float) and abs(val) < 100:
        return f"{val:>12,.2f}"
    return f"{val:>12,}"


def _print_matches(matches):
    """Print a list of metric matches in a table."""
    for match in matches:
        tag = match.get("metric", "?")
        cur = match.get("current_value")
        pri = match.get("prior_value")
        pct = match.get("yoy_change_pct", "N/A")
        dt = match.get("date_type", "?")
        print(f"  [{dt:>3}]  {_fmt_val(cur)}  vs  {_fmt_val(pri)}  ({pct:>7s})  {tag}")


def step1(ticker, year, quarter):
    """Step 1: 8-K Discovery — find the right 8-K and fetch the exhibit."""
    from edgar_8k import fetch_recent_8k_accessions, find_8k_for_period
    from utils import lookup_cik_from_ticker
    from config import HEADERS

    print("=" * 70)
    print(f"STEP 1: 8-K Discovery — {ticker} Q{quarter} {year}")
    print("=" * 70)

    cik = lookup_cik_from_ticker(ticker)
    print(f"\nCIK: {cik}")

    # 1a. Fetch recent 8-Ks
    print(f"\n--- 1a. Recent Item 2.02 8-Ks ---")
    results = fetch_recent_8k_accessions(cik, HEADERS)
    print(f"Found {len(results)} earnings 8-Ks:")
    for r in results:
        print(f"  {r['filing_date']}  acc={r['accession']}  items={r['items']}")

    # 1b. Match to period
    print(f"\n--- 1b. Period matching for Q{quarter} {year} ---")
    entry, html, exhibit_url, period_end = find_8k_for_period(cik, HEADERS, year, quarter)
    if entry:
        print(f"Matched 8-K:")
        print(f"  Filing date: {entry['filing_date']}")
        print(f"  Accession:   {entry['accession']}")
        print(f"  Items:       {entry['items']}")
        print(f"  Period end:  {period_end}")
        print(f"  Exhibit URL: {exhibit_url}")
        print(f"  HTML length: {len(html):,} chars")
    else:
        print(f"  No 8-K found for {ticker} Q{quarter} {year}")

    return entry is not None


def step2(ticker, year, quarter):
    """Step 2: Claude Extraction — extract facts from the exhibit HTML."""
    from edgar_8k import find_8k_for_period, extract_facts_from_8k
    from utils import lookup_cik_from_ticker
    from config import HEADERS

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env file.")
        return False

    print("=" * 70)
    print(f"STEP 2: Claude Extraction — {ticker} Q{quarter} {year}")
    print("=" * 70)

    cik = lookup_cik_from_ticker(ticker)
    entry, html, exhibit_url, period_end = find_8k_for_period(cik, HEADERS, year, quarter)
    if not entry:
        print(f"No 8-K found — run step1 first to diagnose")
        return False

    print(f"Extracting from {exhibit_url}...")
    print(f"HTML: {len(html):,} chars")

    facts = extract_facts_from_8k(html, ticker, year, quarter, full_year_mode=False)
    print(f"\nExtracted {len(facts)} facts")

    # date_type distribution
    dt_counts = {}
    for f in facts:
        dt = f.get("date_type", "None")
        dt_counts[dt] = dt_counts.get(dt, 0) + 1
    print(f"date_type distribution: {dt_counts}")

    collision_count = sum(1 for f in facts if f.get("collision_flag") == 1)
    print(f"Collision flags: {collision_count}/{len(facts)}")

    # Show first 15 facts
    print(f"\n--- First 15 facts ---")
    for f in facts[:15]:
        tag = f.get("tag", "?")
        cur = f.get("visual_current_value")
        pri = f.get("visual_prior_value")
        dt = f.get("date_type", "?")
        col = "*" if f.get("collision_flag") else " "
        print(f"  [{dt:>3}]{col} {_fmt_val(cur)}  {_fmt_val(pri)}  {tag}")

    if len(facts) > 15:
        print(f"  ... ({len(facts) - 15} more)")

    return len(facts) > 0


def step3(ticker, year, quarter):
    """Step 3: End-to-end — get_financials_from_8k + get_metric lookups."""
    from edgar_8k import get_financials_from_8k
    from edgar_tools import get_metric_from_result

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env file.")
        return False

    print("=" * 70)
    print(f"STEP 3: get_financials_from_8k + get_metric — {ticker} Q{quarter} {year}")
    print("=" * 70)

    result = get_financials_from_8k(ticker, year, quarter)
    print(f"\nStatus: {result['status']}")

    if result.get("status") != "success":
        print(f"Error: {result.get('message')}")
        return False

    meta = result.get("metadata", {})
    print(f"Total facts: {meta.get('total_facts')}")
    print(f"Source: {json.dumps(meta.get('source', {}), indent=2)}")

    # Metric lookups — Q
    metrics_q = ["revenue", "net_income", "eps", "operating_income", "gross_profit",
                 "total_assets", "cash", "total_debt"]
    print(f"\n--- Quarterly metrics (date_type=Q) ---")
    for metric in metrics_q:
        m = get_metric_from_result(result, metric, ticker, year, quarter,
                                   full_year_mode=False, date_type="Q")
        if m.get("status") == "success":
            _print_matches(m["matches"])
        else:
            print(f"  {metric:20s}  — {m.get('message', 'not found')}")

    # Metric lookups — FY
    print(f"\n--- Full-year metrics (date_type=FY) ---")
    for metric in ["revenue", "net_income", "eps", "operating_income"]:
        m = get_metric_from_result(result, metric, ticker, year, quarter,
                                   full_year_mode=True, date_type="FY")
        if m.get("status") == "success":
            _print_matches(m["matches"])
        else:
            print(f"  {metric:20s}  — {m.get('message', 'not found')}")

    # Metric lookups — no date_type filter (show all matches)
    print(f"\n--- All matches for 'revenue' (no date_type filter) ---")
    m = get_metric_from_result(result, "revenue", ticker, year, quarter)
    if m.get("status") == "success":
        _print_matches(m["matches"])
    else:
        print(f"  — {m.get('message', 'not found')}")

    return True


def step4(ticker, year, quarter):
    """Step 4: Integration — test through get_financials(source='8k') and get_metric()."""
    from edgar_tools import get_financials, get_metric, get_filings

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env file.")
        return False

    print("=" * 70)
    print(f"STEP 4: get_financials(source='8k') + get_metric() — {ticker} Q{quarter} {year}")
    print("=" * 70)

    # Test get_financials with explicit source
    print(f"\n--- get_financials(source='8k') ---")
    result = get_financials(ticker, year, quarter, source="8k")
    print(f"Status: {result['status']}")
    if result.get("status") == "success":
        meta = result.get("metadata", {})
        print(f"Facts: {meta.get('total_facts')}")
        print(f"Filing type: {meta.get('source', {}).get('filing_type')}")
        print(f"Period end: {meta.get('source', {}).get('period_end')}")
    else:
        print(f"Error: {result.get('message')}")
        return False

    # Test get_metric with explicit source
    print(f"\n--- get_metric(source='8k') ---")
    for metric in ["revenue", "net_income", "eps"]:
        m = get_metric(ticker, year, quarter, metric, source="8k")
        if m.get("status") == "success":
            print(f"  {metric}:")
            _print_matches(m["matches"])
        else:
            print(f"  {metric:20s}  — {m.get('message')}")

    # Test get_filings (should include 8-K entry)
    print(f"\n--- get_filings() ---")
    filings_result = get_filings(ticker, year, quarter)
    if filings_result.get("status") == "success":
        for f in filings_result.get("filings", []):
            form = f.get("form")
            date = f.get("filing_date")
            period = f.get("period_end")
            print(f"  {form:6s}  filed={date}  period_end={period}")
            if f.get("exhibit_url"):
                print(f"         exhibit: {f['exhibit_url']}")

    return True


def main():
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(1)

    step = sys.argv[1].lower()
    ticker = sys.argv[2].upper()
    year = int(sys.argv[3])
    quarter = int(sys.argv[4])

    steps = {
        "step1": step1,
        "step2": step2,
        "step3": step3,
        "step4": step4,
    }

    if step == "all":
        for name in ["step1", "step2", "step3", "step4"]:
            print()
            ok = steps[name](ticker, year, quarter)
            if not ok:
                print(f"\n{name} failed — stopping.")
                sys.exit(1)
            print()
    elif step in steps:
        ok = steps[step](ticker, year, quarter)
        sys.exit(0 if ok else 1)
    else:
        print(f"Unknown step: {step}")
        print("Valid steps: step1, step2, step3, step4, all")
        sys.exit(1)


if __name__ == "__main__":
    main()
