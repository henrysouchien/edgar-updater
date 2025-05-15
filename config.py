#!/usr/bin/env python
# coding: utf-8

# In[2]:


# === CONFIG & SETUP ==========================================

# === HEADERS ======
HEADERS = {
    "User-Agent": "Henry Chien (support@yhenrychien.com)",
    "Accept-Encoding": "gzip, deflate",
}

TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"

# === NUMBER OF FILINGS TO PULL ======
N_10Q = 12
N_10K = 4

# === EXTRA FILING PULLS ===
N_10Q_EXTRA = 0
N_10K_EXTRA = 0

# === SAFE LIMIT TIMES ===
REQUEST_DELAY = 1  # in seconds

# === EXPORTS ===
OUTPUT_METRICS_DIR = "metrics"
EXPORT_UPDATER_DIR = "exports"


