# EDGAR Financial Data Extractor & Updater

A comprehensive Python-based system for extracting, processing, and integrating financial data from SEC EDGAR filings into Excel financial models. This tool automates the extraction of XBRL data from 10-Q and 10-K filings, enriches it with dimensional metadata, and provides seamless integration with Excel workbooks through VBA macros.

## 🎯 Purpose

This system addresses the challenge of manually extracting and updating financial data from SEC filings by providing:

- **Automated XBRL Extraction**: Pulls structured financial data from SEC EDGAR filings
- **Intelligent Data Enrichment**: Categorizes financial periods, maps dimensional axes, and handles presentation roles
- **Excel Integration**: Seamless VBA-powered integration with financial models
- **REST API**: Flask web app with JSON endpoints for financials, filings, and metrics
- **MCP Server**: Claude Code integration via Model Context Protocol
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
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  Claude Code    │───►│  Flask Web App   │───►│  SEC EDGAR API  │
│  (MCP Server)   │    │  (API Endpoints) │    │  (XBRL Data)    │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                ▲
                                │
                       ┌──────────────────┐
                       │  Excel Add-in    │
                       │  (AI Assistant)  │
                       └──────────────────┘
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
├── edgar_pipeline.py          # Core extraction and processing logic (~3500 lines)
├── edgar_8k.py                # 8-K earnings extraction via Claude API
├── edgar_tools.py             # Tool wrappers: get_financials, get_metric, get_filings
├── mcp_server.py              # MCP server for Claude Code integration
├── app.py                     # Flask web app (API + web UI)
├── config.py                  # Configuration settings
├── utils.py                   # Utility functions and helpers
├── enrich.py                  # Data enrichment functions
├── requirements.txt           # Python dependencies
├── Updater_EDGAR.xlsm         # Excel workbook with VBA macros
├── README.md                  # This file
│
├── docs/                      # Documentation
│   ├── MCP_SETUP.md           # MCP server setup & troubleshooting
│   ├── APP_ARCHITECTURE.md    # Flask app architecture
│   ├── CHANGES.md             # Change log
│   ├── 8k-testing-guide.md    # 8-K extraction testing guide
│   ├── test_8k.py             # CLI test runner for 8-K extraction
│   └── plans/                 # Implementation plans
│       └── PLAN-edgar-mcp-refactor.md
│
├── vba/                       # VBA macro source files
│   ├── VBA_mod_GetData.bas
│   └── VBA_mod_UpdateModel.bas
│
├── exports/                   # Generated output files & cache
├── metrics/                   # Pipeline metrics JSON
├── error_logs/                # Error logs (JSON)
├── pipeline_logs/             # Pipeline execution logs
├── usage_logs/                # Usage tracking (request_log.jsonl, claude_api_log.jsonl)
├── notebooks/                 # Jupyter notebooks (analysis)
├── archive/                   # Archived old scripts
│
├── deploy.sh                  # EC2 deployment script
├── update_local.sh            # Local update & zip for deploy
├── update_remote.sh           # Remote update script
├── backup.sh                  # Backup script
├── install_redis.sh           # Redis installation script
├── monitor_usage.py           # Usage monitoring (11-section analytics report)
├── generate_api_key.py        # API key generation
├── valid_tickers.csv          # Valid stock tickers list
├── sp500_tickers.csv          # S&P 500 tickers list
└── valid_keys.json            # API keys configuration
```

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- Excel (Office 365, 2021, or compatible)
- Internet connection for SEC EDGAR access

### MCP Server (Claude Code)

This repo includes an MCP server exposing `get_filings`, `get_financials`, `get_metric`, `list_metrics`, and `search_metrics`.

1. Install dependencies (includes `mcp`):
   ```bash
   pip install -r requirements.txt
   ```
2. Add the MCP server to your Claude config (adjust paths if needed):
   ```json
   {
     "mcpServers": {
       "edgar-financials": {
         "command": "python",
         "args": ["/Users/henrychien/Documents/Jupyter/Edgar_updater/mcp_server.py"],
         "env": {
           "PYTHONPATH": "/Users/henrychien/Documents/Jupyter/Edgar_updater"
         }
       }
     }
   }
   ```

#### MCP Stdout Safety

For `mcp_server.py`, stdout must remain JSON-RPC-only. Any diagnostic output
from tool logic should go to stderr, not stdout.

- `mcp_server.py` routes tool-call stdout to stderr with `redirect_stdout(sys.stderr)`.
- If you add new MCP tools, keep this contract so stdio framing is never polluted.

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

#### Web UI

1. Start the Flask server:
   ```bash
   python app.py
   ```
2. Open `http://localhost:5000` in your browser
3. Enter ticker, year, and quarter, then submit

#### REST API

```bash
# Get financial data as JSON
curl "http://localhost:5000/api/financials?ticker=AAPL&year=2024&quarter=3"

# Get SEC filing metadata
curl "http://localhost:5000/api/filings?ticker=AAPL&year=2024&quarter=3"

# Get a specific metric
curl "http://localhost:5000/api/metric?ticker=AAPL&year=2024&quarter=3&metric_name=revenue"

# Trigger pipeline and download Excel file
curl "http://localhost:5000/trigger_pipeline?ticker=AAPL&year=2024&quarter=3"
```

#### Local API Endpoint Tests

Run endpoint tests without requiring local Redis:

```bash
APP_TESTING=1 FLASK_LIMITER_STORAGE_URI=memory:// python3 -m pytest
```

Current tests live in `tests/test_api_financials.py` and validate:
- invalid key/ticker responses
- CIK lookup failure response shape/status code
- cache behavior (ignore stale error cache, cache successful responses)

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

The heart of the system (~3500 lines) that handles:

- **Filing Discovery**: Fetches recent 10-Q and 10-K filings from SEC EDGAR
- **XBRL Extraction**: Parses inline XBRL data from filing documents
- **Data Enrichment**: Categorizes financial periods and maps dimensional axes
- **Period Matching**: Aligns current and prior period data for analysis
- **4Q Calculations**: Special logic for Q4 full-year calculations

**Key Functions**:
- `run_edgar_pipeline()`: Main entry point (supports both Excel and JSON output)
- `extract_facts_with_document_period()`: XBRL fact extraction
- `enrich_filing()`: Data categorization and enrichment
- `zip_match_in_order()`: Period-to-period data matching

### 2. `edgar_tools.py` - Tool Wrappers

Higher-level functions that wrap the pipeline for MCP and API use:

- **`get_filings(ticker, year, quarter)`**: Fetches SEC filing metadata (URLs, dates, fiscal periods)
- **`get_financials(ticker, year, quarter, full_year_mode)`**: Extracts all financial facts as structured JSON
- **`get_metric(ticker, year, quarter, metric_name, full_year_mode)`**: Gets a specific metric with current/prior values and YoY change
- **`get_metric_from_result(result, metric_name, ...)`**: Filters a metric from pre-fetched financials (avoids re-running pipeline)

**Built-in metric aliases**: `revenue`, `net_income`, `eps`, `gross_profit`, `operating_income`, `cash`, `total_assets`, `total_debt`

### 3. `mcp_server.py` - MCP Server

Exposes five tools via Model Context Protocol for Claude Code/Desktop integration:
- `get_filings` - Filing metadata
- `get_financials` - Full financial data extraction
- `get_metric` - Specific metric lookup
- `list_metrics` - Enumerate available filing metrics/tags for a period
- `search_metrics` - Query and rank likely metrics before calling `get_metric`

**Agent metric discovery workflow**
1. Call `search_metrics` with a natural-language query (examples: `long-term debt`, `operating income`, `diluted eps`).
2. Inspect ranked matches and choose `metric_name` from the top candidate(s).
3. Call `get_metric` with the chosen `metric_name` to fetch values and YoY math.
4. Use `list_metrics` when you need a complete candidate list for a filing period.

`search_metrics` performs fuzzy normalization for:
- hyphen/spacing/camel-case differences (e.g., `long-term debt` -> `LongTermDebt`)
- multi-word phrase matching (e.g., `operating income` -> `OperatingIncomeLoss`)
- common finance abbreviations/aliases (e.g., `eps` -> `EarningsPerShare*`)

### 4. `app.py` - Flask Web Application

Web server providing multiple interfaces:

- **Web UI** (`/`): HTML form for manual extraction
- **JSON API**: `/api/financials`, `/api/filings`, `/api/metric`
- **Excel VBA integration**: `/trigger_pipeline` endpoint
- **Rate limiting**: Three-tier system (public, registered, paid) via Redis
- **Pipeline locking**: Prevents concurrent pipeline executions
- **File caching**: Serves cached results from `exports/`

### 5. `utils.py` - Utility Functions

Provides essential helper functions:

- **CIK Lookup**: `lookup_cik_from_ticker()` - Converts ticker symbols to SEC CIK numbers
- **Date Parsing**: `parse_date()` - Standardizes date formats
- **Dimension Extraction**: `extract_dimensions_from_context()` - Parses XBRL dimensional data
- **Matching Logic**: `run_adaptive_match_keys()` - Intelligent data matching algorithms

### 6. `enrich.py` - Data Enrichment

Enhances extracted data with:

- **Presentation Roles**: `get_concept_roles_from_presentation()` - Maps concepts to presentation hierarchies
- **Negated Labels**: `get_negated_label_concepts()` - Identifies concepts with negative presentations

### 7. VBA Integration (`vba/VBA_mod_GetData.bas`)

Excel automation module that:

- **Input Validation**: Validates user inputs before processing
- **API Integration**: Triggers data extraction via web API
- **File Management**: Handles download and import of processed data
- **Error Handling**: Provides user-friendly error messages

### 8. 8-K Earnings Extraction (`edgar_8k.py`)

For recent earnings (not yet filed in 10-Q/10-K), the system can extract financial data from 8-K Item 2.02 earnings releases using Claude API:

**Key Functions**:
- `get_financials_from_8k()`: Main entry point - discovers 8-K, fetches exhibit, extracts facts
- `_discover_8k_for_period()`: Finds Item 2.02 8-Ks within 150-day window of quarter end
- `_extract_financials_from_exhibit()`: Sends HTML to Claude API for structured extraction

**Features**:
- Automatic HTML preprocessing (strips attributes, reduces size by ~80%)
- Function-level caching in `exports/` directory
- Telemetry logging to `usage_logs/claude_api_log.jsonl` (tokens, cost, duration)
- Supports both quarterly and full-year metrics

**Usage**:
```bash
# Via API
curl "http://localhost:5000/api/financials?ticker=MSCI&year=2025&quarter=4&source=8k"

# Via CLI
python3 test_8k.py all MSCI 2025 4
```

See `docs/8k-testing-guide.md` for detailed testing instructions.

### 9. Model Update Macro (`vba/VBA_mod_UpdateModel.bas`)

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

## 🌐 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET/POST | Web UI form for manual extraction |
| `/api/financials` | GET | JSON financial data (all facts). Add `source=8k` for 8-K extraction |
| `/api/filings` | GET | SEC filing metadata (URLs, dates). Includes 8-K filings |
| `/api/metric` | GET | Specific metric with YoY change. Add `source=8k` for 8-K |
| `/trigger_pipeline` | GET | Excel VBA integration (returns XLSX) |
| `/run_pipeline` | POST | Programmatic JSON API |
| `/download/<filename>` | GET | Download generated files |
| `/generate_key` | POST | Kartra webhook for API key generation |

### Financials Value Fields

`/api/financials` facts include both:
- `current_period_value` and `prior_period_value` (canonical period fields)
- `current_value` and `prior_value` (aliases matching `/api/metric` selection logic: prefer `visual_*`, then period values)

### 8-K Fallback

When a 10-Q/10-K filing isn't available yet (common for recent quarters), `/api/financials` automatically falls back to 8-K extraction. Use `source=8k` to explicitly request 8-K data.

### Rate Limits

| Tier | Limit | Description |
|------|-------|-------------|
| Public | 5 per 7 days | No API key required |
| Registered | 6 per 7 days | Free API key |
| Paid | 500 per 7 days | Paid API key |

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

Enable debug mode via the web UI checkbox or API parameter:

```bash
# Via API
curl "http://localhost:5000/api/financials?ticker=AAPL&year=2024&quarter=3&debug_mode=true"

# Via run_pipeline
curl -X POST http://localhost:5000/run_pipeline \
  -H "Content-Type: application/json" \
  -d '{"ticker":"AAPL","year":2024,"quarter":3,"debug_mode":true}'
```

### Metrics Tracking

The system tracks metrics in the `metrics/` directory during processing:

- **Match Rates**: Success rates for period matching
- **Extraction Counts**: Number of facts extracted and processed
- **Processing Times**: Performance metrics for optimization

### Logging

- **`error_logs/`**: Pipeline error details (JSON)
- **`pipeline_logs/`**: Execution logs per run
- **`usage_logs/`**: Request and usage tracking (JSONL)

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

## 🚢 Deployment

The app runs on EC2 with Gunicorn behind a reverse proxy (Nginx/Caddy) for HTTPS.

### Scripts

- **`deploy.sh`**: Full EC2 deployment
- **`update_local.sh`**: Zips core files for deployment
- **`update_remote.sh`**: Uploads and restarts on remote server
- **`backup.sh`**: Backs up the project
- **`install_redis.sh`**: Sets up Redis for rate limiting

### Environment Variables

The `.env` file is created by `deploy.sh`. After deployment, set:

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | Yes (for 8-K extraction) | Claude API key for parsing 8-K earnings releases |
| `KARTRA_APP_ID` | Optional | Kartra integration |
| `KARTRA_API_KEY` | Optional | Kartra integration |
| `ADMIN_KEY` | Optional | Admin endpoint auth |

### Production

```bash
# Build deployment package
./update_local.sh

# Deploy to EC2
./update_remote.sh

# After deploy, set the Anthropic API key on the instance:
# Edit .env and fill in ANTHROPIC_API_KEY=sk-ant-...
```

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
