#!/usr/bin/env python3
"""
Refresh valid_tickers.csv from SEC filings.

Only includes companies that filed 10-K or 10-Q in a recent quarter,
excluding foreign filers (20-F, 6-K).

Usage:
    python3 refresh_tickers.py              # Uses latest available quarter
    python3 refresh_tickers.py 2025 QTR1    # Specific quarter

Note: Ticker counts may vary by quarter depending on filing activity.
      Original notebook (notebooks/ticker-list.ipynb) used 2024/QTR4.
"""

import requests
import gzip
from io import BytesIO
import time
import sys
import os
from datetime import datetime

# === CONFIG ===
REQUEST_DELAY = 0.5
HEADERS = {
    "User-Agent": "Henry Chien (support@henrychien.com)",
    "Accept-Encoding": "gzip, deflate",
}
TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "valid_tickers.csv")


def get_latest_quarter():
    """Determine the latest available SEC quarter based on current date."""
    now = datetime.now()
    year = now.year
    month = now.month

    # SEC quarters: QTR1 (Jan-Mar), QTR2 (Apr-Jun), QTR3 (Jul-Sep), QTR4 (Oct-Dec)
    # Use the previous quarter since current quarter may be incomplete
    if month <= 3:
        return year - 1, "QTR4"
    elif month <= 6:
        return year, "QTR1"
    elif month <= 9:
        return year, "QTR2"
    else:
        return year, "QTR3"


def download_master_index(year: int, quarter: str) -> set:
    """Download SEC master index and extract CIKs of 10-K/10-Q filers."""
    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/{quarter}/master.gz"
    print(f"Downloading: {url}")

    response = requests.get(url, headers=HEADERS)
    time.sleep(REQUEST_DELAY)
    response.raise_for_status()

    seen_ciks = set()

    with gzip.open(BytesIO(response.content), 'rt', encoding='latin-1') as f:
        started = False
        for line in f:
            if not started:
                if line.strip().startswith("CIK|"):
                    started = True
                continue

            parts = line.strip().split("|")
            if len(parts) != 5:
                continue

            cik, company_name, form_type, date_filed, _ = parts

            # Only include 10-K and 10-Q filers (excludes 20-F, 6-K, etc.)
            if form_type in {"10-K", "10-Q"}:
                seen_ciks.add(cik.zfill(10))

    print(f"Found {len(seen_ciks)} unique CIKs with 10-K/10-Q filings")
    return seen_ciks


def download_ticker_map() -> dict:
    """Download SEC's CIK-to-ticker mapping."""
    print(f"Downloading ticker map from SEC...")

    response = requests.get(TICKER_CIK_URL, headers=HEADERS)
    time.sleep(REQUEST_DELAY)
    response.raise_for_status()

    data = response.json()
    print(f"Loaded {len(data)} entries from SEC ticker map")

    # Build CIK -> ticker lookup
    cik_to_ticker = {}
    for entry in data.values():
        cik = str(entry["cik_str"]).zfill(10)
        ticker = entry["ticker"]
        cik_to_ticker[cik] = ticker

    return cik_to_ticker


def refresh_tickers(year: int = None, quarter: str = None):
    """Main function to refresh valid_tickers.csv."""

    # Use latest quarter if not specified
    if year is None or quarter is None:
        year, quarter = get_latest_quarter()

    print(f"\n=== Refreshing valid_tickers.csv ===")
    print(f"Quarter: {year}/{quarter}\n")

    # Step 1: Get CIKs of 10-K/10-Q filers
    valid_ciks = download_master_index(year, quarter)

    # Step 2: Map CIKs to tickers
    cik_to_ticker = download_ticker_map()

    # Step 3: Get tickers for valid CIKs
    valid_tickers = set()
    for cik in valid_ciks:
        ticker = cik_to_ticker.get(cik)
        if ticker:
            valid_tickers.add(ticker)

    print(f"Matched {len(valid_tickers)} tickers")

    # Step 4: Export to CSV
    sorted_tickers = sorted(valid_tickers)
    with open(OUTPUT_FILE, 'w') as f:
        f.write("ticker\n")
        for ticker in sorted_tickers:
            f.write(f"{ticker}\n")

    print(f"\nExported {len(sorted_tickers)} valid tickers to {OUTPUT_FILE}")
    return sorted_tickers


if __name__ == "__main__":
    year = None
    quarter = None

    if len(sys.argv) >= 3:
        year = int(sys.argv[1])
        quarter = sys.argv[2]
        if not quarter.startswith("QTR"):
            quarter = f"QTR{quarter}"

    refresh_tickers(year, quarter)
