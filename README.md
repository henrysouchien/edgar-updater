# Financial Model Updater — Codebase Overview

## 🔧 Purpose
Automated system to extract, enrich, and inject financial data from SEC EDGAR filings into Excel models.

## 🗺️ High-Level Architecture

- **Flask app (`app.py`)** handles key-based requests and file generation
- **Kartra** manages user registration, email delivery, and tagging
- **`edgar_pipeline.py`** performs the data extraction and enrichment
- **`monitor_usage.py`** (admin script) provides a CLI dashboard to monitor usage, errors, and upgrade candidates
- **Excel workbook (`Updater_EDGAR.xlsm`)** contains VBA macros to integrate updated data into analyst models

## 🧩 Key Components

### 1. `app.py` (Flask entry point)
- Routes: `/run_pipeline`, `/download_file`, etc.
- Triggers data pipeline and serves Excel files

### 2. `edgar_pipeline.py`
- Core pipeline logic (fetching, parsing, matching, exporting)

### 3. `monitor_usage.py`
- CLI tool to review usage, detect errors, highlight upgrade candidates

## 🗃 File Tree (Simplified)

project/
├── app.py
├── edgar_pipeline.py
├── monitor_usage.py
├── templates/
├── static/
├── Updater_EDGAR.xlsm
├── README.md

## Usage
- **`run_edgar_pipeline.py`** is a local script to manually run the pipeline (for dev/testing)

```bash
python run_edgar_pipeline.py --ticker MSCI --year 2023 --quarter 2
```