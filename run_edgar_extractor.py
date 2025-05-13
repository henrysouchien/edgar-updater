#!/usr/bin/env python
# coding: utf-8

# In[ ]:


# === FUNCTION TO RUN EDGAR EXTRACTOR FROM COMAMAND LINE ==========================================

import sys
from edgar_pipeline import run_edgar_pipeline

def parse_args():
    if len(sys.argv) < 4:
        print("\n❌ Missing arguments.")
        print("🧠 Usage: python run_edgar_extractor.py <TICKER> <YEAR> <QUARTER> [FY] [DEBUG]")
        print("🔢 QUARTER: 1Q, 2Q, 3Q, 4Q or just 1, 2, 3, 4 (case-insensitive)")
        print("📦 Optional: add 'FY' to run full-year mode")
        print("🐞 Optional: add 'DEBUG' to enable debug mode")
        print("✅ Example: python run_edgar_extractor.py NVDA 2023 2Q FY DEBUG\n")
        sys.exit(1)

    ticker = sys.argv[1]
    year = int(sys.argv[2])

    q_arg = sys.argv[3].lower().replace("q", "")
    if q_arg not in {"1", "2", "3", "4"}:
        print(f"\n❌ Invalid quarter value: '{sys.argv[3]}'")
        print("🔢 Must be one of: 1Q, 2Q, 3Q, 4Q or just 1, 2, 3, 4 (case-insensitive)")
        print("✅ Example: python run_edgar_extractor.py AAPL 2022 3Q\n")
        sys.exit(1)
    quarter = int(q_arg)

    # Defaults
    full_year_mode = False
    debug_mode = False

    # Parse optional flags
    for arg in sys.argv[4:]:
        arg_lc = arg.strip().lower()
        if arg_lc == "fy":
            full_year_mode = True
        elif arg_lc == "debug":
            debug_mode = True

    return ticker, year, quarter, full_year_mode, debug_mode

if __name__ == "__main__":
    ticker, year, quarter, full_year_mode, debug_mode = parse_args()

    run_edgar_pipeline(
        ticker=ticker,
        year=year,
        quarter=quarter,
        full_year_mode=full_year_mode,
        debug_mode=debug_mode,
        excel_file="Updater_EDGAR.xlsm",
        sheet_name="Raw_data"
    )

