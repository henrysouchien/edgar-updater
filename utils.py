#!/usr/bin/env python
# coding: utf-8

# In[13]:


from datetime import datetime, timedelta
from lxml import etree
import pandas as pd
import re

from config import (
    TICKER_CIK_URL,
    HEADERS
)


# In[3]:


# === METRICS LOGGER ===
# Usage:
#   log_metric("match_rate", {"fy": 0.94, "ytd": 0.88})     → stores a sub-dictionary
#   log_metric("negated_labels", 30)                        → stores single value

metrics = {}

def log_metric(key, value):
    """
    Logs a named metric or dictionary of sub-metrics to the global metrics store.

    Args:
        key (str): Metric name to log (e.g. "match_rate").
        value (float, int, or dict): Metric value or sub-metric dictionary to store.

    Behavior:
        - If value is a dict and an existing dict is already stored, updates it.
        - Otherwise, overwrites the key with the given value.

    Example:
        log_metric("match_rate", {"fy": 0.94, "ytd": 0.88})
        log_metric("negated_labels", 30)
    """
    global metrics
    if isinstance(value, dict) and isinstance(metrics.get(key), dict):
        metrics[key].update(value)
    else:
        metrics[key] = value


# In[4]:


# === NEW Helper: Extract Fiscal Year End ===
def extract_fiscal_year_end(facts):
    for fact in facts:
        if fact.get("tag") == "dei:CurrentFiscalYearEndDate":
            return fact.get("value")
    return None


# In[ ]:


# === NEW Helper: Extract Dimensions from Context ===

def extract_dimensions_from_context(context_html):
    """
    Parses a raw XBRL context block (as HTML string) and extracts all dimensional metadata,
    including axis-member pairs and their XML locations.

    Args:
        context_html (str): Raw HTML/XML content of an <xbrli:context> block.

    Returns:
        list of dict: Each dictionary contains:
            - 'dimension': Full dimension QName
            - 'member': Full member QName
            - 'axis_name': Shortened axis name (e.g. "Segment")
            - 'member_name': Shortened member name (e.g. "SoftwareDivision")
            - 'location': Raw XML string of the dimension tag

    Example:
        extract_dimensions_from_context(context_html) -> [{"dimension": "...", "member": "...", ...}]
    """
    dimensions = []
    try:
        # Inject required namespaces into root wrapper
        wrapped_xml = f'''
        <root
          xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
          xmlns:us-gaap="http://fasb.org/us-gaap/2024-01-31"
          xmlns:srt="http://fasb.org/srt"
        >
        {context_html}
        </root>
        '''

        # Parse with lxml
        ctx_tree = etree.fromstring(wrapped_xml.encode())

        # Namespace-aware path to <segment>
        segment = ctx_tree.find(".//{http://www.xbrl.org/2003/instance}segment")
        if segment is None:
            return []

        # Robust extraction: match all tags named 'explicitmember', case-insensitive
        members = segment.xpath("*[local-name()='explicitmember']")

        for member in members:
            dim = member.get("dimension")
            member_val = member.text

            axis_name = dim.split(":")[-1] if dim else None
            member_name = member_val.split(":")[-1] if member_val else None
            location = etree.tostring(member, encoding="unicode").strip()

            dimensions.append({
                "dimension": dim.strip() if dim else None,
                "member": member_val.strip() if member_val else None,
                "axis_name": axis_name,
                "member_name": member_name,
                "location": location
            })

    except Exception as e:
        print(f"❌ Failed to parse context block: {e}")
    return dimensions


# In[5]:


# === Zip Match Output Standardizer ===

AXIS_COLS = [
    "axis_consolidation", "axis_segment", "axis_product",
    "axis_geo", "axis_legal_entity", "axis_unassigned"
]

PREFIX_MAP = {
    "tag": "current_tag",
    "date_type": "current_date_type",
    "start_current": "current_start",
    "end_current": "current_end",
    "current_period_value": "current_value",
    "contextref_current": "current_contextref",
    "start_prior": "prior_start",
    "end_prior": "prior_end",
    "prior_period_value": "prior_value",
    "contextref_prior": "prior_contextref",
    "presentation_role": "current_presentation_role",
    "axis_consolidation": "current_axis_consolidation",
    "axis_segment": "current_axis_segment",
    "axis_product": "current_axis_product",
    "axis_geo": "current_axis_geo",
    "axis_legal_entity": "current_axis_legal_entity",
    "axis_unassigned": "current_axis_unassigned",
}

FINAL_COLS = [
    "tag", "date_type",
    *AXIS_COLS,
    "start_current", "end_current", "current_period_value", "contextref_current",
    "start_prior", "end_prior", "prior_period_value", "contextref_prior",
    "presentation_role"
]


# In[6]:


# === CONFIG & SETUP ==========================================
# === Helper: Lookup CIK from ticker ===
import requests

def lookup_cik_from_ticker(ticker):
    """
    Retrieves the SEC CIK (Central Index Key) for a given stock ticker symbol.

    Args:
        ticker (str): Stock ticker symbol (e.g. "AAPL").

    Returns:
        str or None: 10-digit CIK as a zero-padded string if found, otherwise None.

    Notes:
        - Uses the public SEC JSON mapping at https://www.sec.gov/files/company_tickers.json.
        - Requires a valid User-Agent header.
    """
    try:
        r = requests.get(TICKER_CIK_URL, headers=HEADERS)
        r.raise_for_status()
        data = r.json()

        print(f"✅ Successfully pulled {len(data)} ticker entries from SEC database to check CIK.")
        
        # Validate structure before using
        first_entry = list(data.values())[0]
        if not ("ticker" in first_entry and "cik_str" in first_entry):
            print("❌ SEC JSON structure unexpected — keys missing.")
            return None
        
        ticker = ticker.lower()
        for entry in data.values():
            if entry["ticker"].lower() == ticker:
                cik_int = int(entry["cik_str"])
                return str(cik_int).zfill(10)
        
        print(f"⚠️ Ticker '{ticker}' not found in SEC database.")
        return None
    except Exception as e:
        print(f"❌ Error looking up CIK: {e}")
        return None


# In[7]:


# === CONFIG & SETUP ==========================================
# === Helper: Zip match method to perfom sequential group matching of dataframes in order ===
def zip_match_in_order(df_curr, df_prior, match_keys):
    """
    Performs sequential, row-aligned zip matching between current and prior DataFrames,
    grouped by shared match_keys.

    Args:
        df_curr (DataFrame): Current period DataFrame.
        df_prior (DataFrame): Prior period DataFrame.
        match_keys (list of str): Column names to group and align on.

    Returns:
        DataFrame: Row-aligned merged DataFrame with prefixed columns (e.g. current_tag, prior_tag).
    """

    matched_rows = []

    curr_groups = df_curr.groupby(match_keys)
    prior_groups = df_prior.groupby(match_keys)

    for key in curr_groups.groups.keys():
        curr_rows = curr_groups.get_group(key).reset_index(drop=True)
        
        if key not in prior_groups.groups:
            continue
            
        prior_rows = prior_groups.get_group(key).reset_index(drop=True)
        min_len = min(len(curr_rows), len(prior_rows))

        for i in range(min_len):
            row = pd.concat([
                curr_rows.iloc[[i]].add_prefix("current_"),
                prior_rows.iloc[[i]].add_prefix("prior_")
            ], axis=1)
            matched_rows.append(row)

    return pd.concat(matched_rows, ignore_index=True) if matched_rows else pd.DataFrame()


# In[8]:


# === MATCH LOGIC (Audit Value Collisions) =============
# === Helper: Check for duplicate values that have multiple matches (i.e. one current value to many period values) ===

from collections import defaultdict

def audit_value_collisions(df):
    """
    Audits the matched DataFrame to detect value collisions, where the same value is used
    in multiple non-unique pairings (e.g., one prior → many current or vice versa).

    Args:
        df (DataFrame): Matched DataFrame containing "prior_period_value" and "current_period_value".

    Returns:
        DataFrame: Subset of rows flagged for collision (non-unique mappings).

    Example:
        flagged = audit_value_collisions(matched_df)
    """
    prior_to_current = defaultdict(set)
    current_to_prior = defaultdict(set)

    # Build mapping: prior → set of unique currents, current → set of unique priors
    for _, row in df.iterrows():
        prior = row["prior_period_value"]
        current = row["current_period_value"]
        if pd.notnull(prior) and pd.notnull(current):
            prior_to_current[prior].add(current)
            current_to_prior[current].add(prior)

    # Only flag if value appears in >1 unique pairing
    bad_prior_values = {p for p, c_set in prior_to_current.items() if len(c_set) > 1}
    bad_current_values = {c for c, p_set in current_to_prior.items() if len(p_set) > 1}

    # Now isolate rows where those mismatches happen
    flagged_df = df[
        df["prior_period_value"].isin(bad_prior_values) |
        df["current_period_value"].isin(bad_current_values)
    ].copy()
    flagged_df = flagged_df.sort_values(by=["tag", "current_period_value", "prior_period_value"])

    print(f"🚨 Prior values used in >1 unique current matches: {len(bad_prior_values)}")
    print(f"🚨 Current values used in >1 unique prior matches: {len(bad_current_values)}")
    print(f"📊 Total flagged rows: {len(flagged_df)}")

    #display(flagged_df[["tag", "current_period_value", "prior_period_value"]])
    return flagged_df


# In[9]:


# === MATCH UTILITIES ============================================= 
# === Adaptive Fallback Match Key Logic ===

def run_adaptive_match_keys(curr_df, prior_df, match_keys, min_keys):
    """
    Iteratively drops weak match keys until a sufficient shared key overlap is found
    between the current and prior DataFrames.

    Args:
        curr_df (DataFrame): Current period DataFrame.
        prior_df (DataFrame): Prior period DataFrame.
        match_keys (list of str): Initial list of match keys to test.
        min_keys (list of str): Minimum required match keys to retain.

    Returns:
        list of str: Optimized list of match keys with sufficient shared overlap.
    """
    match_keys = match_keys.copy()

    while True:
        curr_keys = set(curr_df.groupby(match_keys).groups.keys())
        prior_keys = set(prior_df.groupby(match_keys).groups.keys())
        shared_keys = curr_keys & prior_keys

        shared_ratio = len(shared_keys) / max(len(curr_keys), 1)
        print(f"🔍 Matching on: {match_keys}")
        print(f"   • Current keys: {len(curr_keys)}")
        print(f"   • Prior keys  : {len(prior_keys)}")
        print(f"   • Shared keys : {len(shared_keys)} ({shared_ratio:.2%} of current)")

        if shared_ratio < 0.05 and len(match_keys) > len(min_keys):
            print(f"⚠️ Too few shared keys — dropping: '{match_keys[-1]}'")
            match_keys = match_keys[:-1]
        else:
            print("✅ Match keys selected.\n")
            return match_keys

# === Zip Match Output Standardizer ===

def standardize_zip_output(df):
    """
    Renames the matched columns using PREFIX_MAP as keys for matches
    Ensures all FINAL_COLS are present (filled with None if missing).
    Preserves extra columns and appends them after FINAL_COLS.

    Standardizes column names in a matched DataFrame using PREFIX_MAP,
    ensures all FINAL_COLS are present, and reorders columns accordingly.

    Args:
        df (DataFrame): DataFrame with prefixed current/prior columns.

    Returns:
        DataFrame: Cleaned and standardized DataFrame ready for export or analysis.
    """
    
    prefix_map = PREFIX_MAP
    final_cols = FINAL_COLS

    # Step 1: Rename using prefix_map
    rename_map = {v: k for k, v in prefix_map.items() if v in df.columns}
    missing = [v for k, v in prefix_map.items() if v not in df.columns]
    
    #if missing:
        #print(f"⚠️ Skipped missing columns during rename: {missing}")

    df = df.rename(columns=rename_map)

    # Step 2: Fill any missing expected cols with None
    for col in final_cols:
        if col not in df.columns:
            df[col] = None

    # Step 3: Reorder to have FINAL_COLS first, preserve any extra cols after
    ordered_cols = final_cols + [c for c in df.columns if c not in final_cols]
    return df[ordered_cols]


# In[11]:


from dateutil.parser import parse
import datetime

def parse_date(date_input):

    """
    Safely parses a string or datetime object into a `datetime.date` object.

    Args:
        date_input (str or datetime-like): Input date string or object.

    Returns:
        datetime.date or None: Parsed date if successful, otherwise None.

    Example:
        parse_date("2023-03-31") -> datetime.date(2023, 3, 31)
    """
    
    if isinstance(date_input, datetime.date):
        return date_input  # Already safe
    try:
        return parse(date_input).date()
        
    except Exception:
        try:
            return datetime.datetime.strptime(date_input, "%m/%d/%Y").date()
        except Exception:
            print(f"⚠️ Unrecognized date format: {date_input}")
            return None


# In[ ]:




