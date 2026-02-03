# EDGAR Financial Data Extractor & Updater

A comprehensive Python-based system for extracting, processing, and integrating financial data from SEC EDGAR filings into Excel financial models. This tool automates the extraction of XBRL data from 10-Q and 10-K filings, enriches it with dimensional metadata, and provides seamless integration with Excel workbooks through VBA macros.

## 🎯 Purpose

This system addresses the challenge of manually extracting and updating financial data from SEC filings by providing:

- **Automated XBRL Extraction**: Pulls structured financial data from SEC EDGAR filings
- **Intelligent Data Enrichment**: Categorizes financial periods, maps dimensional axes, and handles presentation roles
- **Excel Integration**: Seamless VBA-powered integration with financial models
- **Quarterly & Annual Workflows**: Supports both quarterly (10-Q) and annual (10-K) filing processing
- **4Q Calculation Support**: Special handling for Q4 filings with full-year calculations

## 🏗️ Architecture Overview

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Excel Model   │◄──►│  VBA Macros      │◄──►│  Python Pipeline│
│   (Updater.xlsm)│    │  (GetData.bas)   │    │  (edgar_pipeline)│
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │                        │
                                ▼                        ▼
                       ┌──────────────────┐    ┌─────────────────┐
                       │  Flask Web App   │    │  SEC EDGAR API  │
                       │  (API Endpoints) │    │  (XBRL Data)    │
                       └──────────────────┘    └─────────────────┘
```

## 🔗 Related: Excel Add-in (AI Chat Interface)

A separate package provides an AI-powered chat interface for Excel:

**Location:** `../AI-excel-addin/`

This Office Add-in allows Claude to:
- Chat with users in Excel's task pane
- Read/write spreadsheet cells via Office.js
- Fetch SEC financial data via this backend's API

See `../AI-excel-addin/ARCHITECTURE.md` for details. The add-in connects to `app.py` endpoints defined in `PLAN-edgar-mcp-refactor.md`.

## 📁 Project Structure

```
Edgar_updater/
├── edgar_pipeline.py          # Core extraction and processing logic
├── run_edgar_extractor.py     # Command-line interface
├── config.py                  # Configuration settings
├── utils.py                   # Utility functions and helpers
├── enrich.py                  # Data enrichment functions
├── export_final.py            # Export and finalization logic
├── requirements.txt           # Python dependencies
├── Updater_EDGAR.xlsm         # Excel workbook with VBA macros
├── VBA_mod_GetData.bas        # VBA module for data retrieval
├── VBA_mod_UpdateModel.bas    # VBA module for model updates
└── README.md                  # This file
```

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- Excel (Office 365, 2021, or compatible)
- Internet connection for SEC EDGAR access

### Installation

1. **Clone or download the project**
2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure the Excel workbook**:
   - Open `Updater_EDGAR.xlsm`
   - Enable macros when prompted
   - Configure inputs in the "Raw_data" sheet

### Basic Usage

#### Command Line Interface

Extract data for a specific company and period:

```bash
# Basic quarterly extraction
python run_edgar_extractor.py AAPL 2023 2

# Full-year mode (Q4 only)
python run_edgar_extractor.py AAPL 2023 4 FY

# With debug mode
python run_edgar_extractor.py AAPL 2023 2 DEBUG
```

#### Excel Integration

1. **Set up inputs** in the "Raw_data" sheet:
   - **H1**: Ticker symbol (e.g., "AAPL")
   - **H2**: Fiscal year (e.g., "2023")
   - **H3**: Quarter (1-4)
   - **H4**: Full Year Mode (True/False)
   - **G13**: API Key (use "public" for testing)
   - **G15**: Download folder path

2. **Run the macro**:
   - Press `Alt+F8` (Windows) or `Option+F8` (Mac)
   - Select `GetData` and click "Run"

## 🔧 Core Components

### 1. `edgar_pipeline.py` - Main Processing Engine

The heart of the system that handles:

- **Filing Discovery**: Fetches recent 10-Q and 10-K filings from SEC EDGAR
- **XBRL Extraction**: Parses inline XBRL data from filing documents
- **Data Enrichment**: Categorizes financial periods and maps dimensional axes
- **Period Matching**: Aligns current and prior period data for analysis
- **4Q Calculations**: Special logic for Q4 full-year calculations

**Key Functions**:
- `run_edgar_pipeline()`: Main entry point
- `extract_facts_with_document_period()`: XBRL fact extraction
- `enrich_filing()`: Data categorization and enrichment
- `zip_match_in_order()`: Period-to-period data matching

### 2. `utils.py` - Utility Functions

Provides essential helper functions:

- **CIK Lookup**: `lookup_cik_from_ticker()` - Converts ticker symbols to SEC CIK numbers
- **Date Parsing**: `parse_date()` - Standardizes date formats
- **Dimension Extraction**: `extract_dimensions_from_context()` - Parses XBRL dimensional data
- **Matching Logic**: `run_adaptive_match_keys()` - Intelligent data matching algorithms

### 3. `enrich.py` - Data Enrichment

Enhances extracted data with:

- **Presentation Roles**: `get_concept_roles_from_presentation()` - Maps concepts to presentation hierarchies
- **Negated Labels**: `get_negated_label_concepts()` - Identifies concepts with negative presentations

### 4. VBA Integration (`VBA_mod_GetData.bas`)

Excel automation module that:

- **Input Validation**: Validates user inputs before processing
- **API Integration**: Triggers data extraction via web API
- **File Management**: Handles download and import of processed data
- **Error Handling**: Provides user-friendly error messages

### 5. Model Update Macro (`VBA_mod_UpdateModel.bas`)

The `UpdateModel` macro automates updating your financial model with extracted EDGAR data. It matches values from the Raw_data sheet and writes corresponding values into your model.

#### Modes

| Mode | Setting (G19) | Use Case |
|------|---------------|----------|
| **Normal** | FALSE or blank | Update current year column using prior year as reference |
| **Reverse** | TRUE | Backfill prior year column using current year as reference |

#### Settings (Raw_data Sheet)

| Cell | Setting | Description |
|------|---------|-------------|
| G11 | Conversion Factor | Scale factor for values (e.g., 1000 for values in thousands) |
| G19 | Reverse Mode | TRUE = reverse mode, FALSE/blank = normal mode |
| G22 | Collision Rate | Pre-calculated rate of duplicate matches (auto-populated) |

#### Workflow

1. **Run the macro**: Press `Alt+F8` → Select `UpdateModel` → Run
2. **Select source range**: Choose the column with values to match against
3. **Select target cell**: Choose the first cell of the column to update
4. **Review results**:
   - Yellow highlighted cells = potential collisions or missing matches
   - Check the Immediate Window (Ctrl+G) for debug output

#### Features

- **Sign Flipping**: Automatically detects when values are negated (e.g., expenses)
- **Formula Handling**: Parses formulas and replaces numeric tokens with matched values
- **Collision Detection**: Flags cells where multiple values could match
- **Format Preservation**: Copies formatting from source to target range

## 📊 Data Processing Workflow

### Standard Quarterly Workflow (Q1-Q3)

1. **Filing Discovery**: Find relevant 10-Q filings for the specified quarter
2. **XBRL Extraction**: Parse inline XBRL data from filing documents
3. **Data Enrichment**: Categorize facts into periods (current_q, prior_q, etc.)
4. **Period Matching**: Align current and prior quarter data
5. **Export**: Generate CSV files and Excel integration data

### Q4/Annual Workflow

1. **10-K Processing**: Extract data from annual 10-K filing
2. **Full-Year Calculations**: Calculate 4Q values from FY and YTD data
3. **Multi-Period Matching**: Match FY, YTD, and instant data points
4. **Fuzzy Matching**: Fallback matching for complex dimensional data
5. **Comprehensive Export**: Multiple output files for different use cases

## ⚙️ Configuration

### `config.py` Settings

```python
# SEC API Configuration
HEADERS = {
    "User-Agent": "Henry Chien (support@henrychien.com)",
    "Accept-Encoding": "gzip, deflate",
}

# Filing Limits
N_10Q = 12          # Number of 10-Q filings to fetch
N_10K = 4           # Number of 10-K filings to fetch
REQUEST_DELAY = 1   # Delay between API requests (seconds)
```

### Excel Configuration

The Excel workbook (`Updater_EDGAR.xlsm`) contains:

- **Raw_data Sheet**: Input configuration and data storage
- **VBA Macros**: Automation for data retrieval and model updates
- **Template Structure**: Pre-configured ranges for financial modeling

## 📈 Output Files

The system generates several output files:

- **`{TICKER}_{YEAR}Q{QUARTER}_extracted_facts_full.csv`**: Complete extracted data
- **`{TICKER}_{YEAR}Q{QUARTER}_matched_full.csv`**: Period-matched data
- **`{TICKER}_{YEAR}Q{QUARTER}_4Q_calculated_output.csv`**: 4Q calculations (Q4 only)
- **Excel Integration File**: Direct import into financial models

## 🔍 Debugging & Monitoring

### Debug Mode

Enable debug mode to get detailed processing information:

```bash
python run_edgar_extractor.py AAPL 2023 2 DEBUG
```

### Metrics Tracking

The system tracks various metrics during processing:

- **Match Rates**: Success rates for period matching
- **Extraction Counts**: Number of facts extracted and processed
- **Processing Times**: Performance metrics for optimization

## 🛠️ Advanced Features

### Fuzzy Matching

For complex dimensional data, the system includes fuzzy matching algorithms:

- **Adaptive Keys**: Automatically adjusts matching criteria
- **Fallback Logic**: Handles cases where exact matches fail
- **Quality Scoring**: Provides confidence scores for matches

### 4Q Calculation Logic

Special handling for Q4 filings:

- **FY vs YTD Matching**: Aligns full-year and year-to-date data
- **4Q Calculation**: Computes Q4 values from FY - YTD
- **Instant Data**: Handles balance sheet and other instant data points

## 🤝 Contributing

This project is designed for financial analysts and developers working with SEC EDGAR data. To contribute:

1. **Fork the repository**
2. **Create a feature branch**
3. **Make your changes**
4. **Test thoroughly** with different tickers and periods
5. **Submit a pull request**

## 📞 Support

For questions, issues, or feature requests:

- **Email**: support@henrychien.com
- **Documentation**: Check the inline comments in the code
- **Debug Mode**: Use debug mode for detailed processing information

## 📄 License

This project is provided as-is for educational and professional use. Please ensure compliance with SEC EDGAR usage policies and respect rate limits.

## 🔗 Related Resources

- [SEC EDGAR Database](https://www.sec.gov/edgar/searchedgar/companysearch)
- [XBRL Documentation](https://www.xbrl.org/)
- [SEC Filing Types](https://www.sec.gov/fast-answers/answersform10khtm.html)

---

**Note**: This tool is designed to streamline financial data extraction and should be used in conjunction with proper financial analysis practices. Always verify extracted data against source filings for critical applications.