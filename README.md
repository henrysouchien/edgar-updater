# edgar-updater

This tool extracts financial data from SEC EDGAR 10-Q and 10-K filings, categorizes the data, then updates Excel financial models with VBA.

## Features

- Parses XBRL data from SEC filings
- Matches and categorizes key financial facts
- Exports to Excel and updates model seamlessly

## Usage

```bash
python run_edgar_pipeline.py --ticker MSCI --year 2023 --quarter 2