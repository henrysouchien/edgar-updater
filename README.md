# edgar-updater

This tool automatically extracts financial data from SEC EDGAR 10-Q and 10-K filings, enriches the data, and updates Excel models.

## Features

- Parses XBRL data from SEC filings
- Matches and categorizes key financial facts
- Exports to Excel for seamless model updates

## Usage

```bash
python run_edgar_pipeline.py --ticker MSCI --year 2023 --quarter 2