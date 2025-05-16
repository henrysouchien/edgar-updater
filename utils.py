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
    Logs a named metric or sub-metric dictionary to the global `metrics` store for tracking runtime stats,
    quality diagnostics, or workflow flags.

    Args:
        key (str): Name of the metric (e.g., "match_rate", "fallback_triggered").
        value (float, int, or dict): Metric value or dictionary of sub-metrics to log.

    Behavior:
        - If `value` is a dictionary and the key already exists with a dict value, it merges (updates) keys.
        - Otherwise, the key is overwritten with the new value.

    Side Effects:
        - Updates the global `metrics` dictionary in-place.
        - Used for logging and debugging purposes across extraction, matching, fallback, etc.

    Example:
        log_metric("match_rate", {"fy": 0.94, "ytd": 0.88})
        log_metric("negated_labels", 30)
        log_metric("fallback_triggered", True)
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
    Parses a raw XBRL <xbrli:context> block and extracts all dimensional metadata, including
    axis-member (dimension-member) pairs and their associated XML structure.

    This function wraps the input HTML fragment in a root element with required XBRL namespaces,
    locates the <segment> section, and extracts all <xbrldi:explicitMember> entries. Each entry is 
    returned as a structured dictionary useful for downstream axis tagging and analytics.

    Args:
        context_html (str): Raw XML/HTML string of a single <xbrli:context> block.

    Returns:
        list of dict: Each dictionary contains:
            - 'dimension': Full QName of the dimension (e.g., "us-gaap:StatementBusinessSegmentsAxis")
            - 'member': Full QName of the member (e.g., "us-gaap:PlatformDivisionMember")
            - 'axis_name': Simplified axis identifier (e.g., "StatementBusinessSegmentsAxis")
            - 'member_name': Simplified member identifier (e.g., "PlatformDivisionMember")
            - 'location': Raw XML string of the <explicitMember> node

    Notes:
        - Gracefully handles missing <segment> blocks.
        - Returns an empty list if no dimensions are found or parsing fails.
        - Requires `lxml` for namespace-aware XML parsing.

    Example:
        dims = extract_dimensions_from_context(context_html)
        dims[0]["axis_name"]  # → "ProductOrServiceAxis"
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
    Looks up the SEC Central Index Key (CIK) for a given stock ticker using the SEC's public
    company-to-CIK mapping.

    This function fetches and parses the official JSON file hosted by the SEC, then searches
    for a case-insensitive match to the given ticker symbol. If found, it returns the CIK
    as a zero-padded 10-digit string suitable for EDGAR queries.

    Args:
        ticker (str): Stock ticker symbol (e.g., "AAPL", "MSFT").

    Returns:
        str or None: 10-digit CIK string (e.g., "0000320193") if found, otherwise None.

    Notes:
        - Requires the global constant `TICKER_CIK_URL` to be defined.
        - Uses the global `HEADERS` dict for HTTP request (must include User-Agent).
        - Returns None and logs an error if the SEC JSON structure is invalid or lookup fails.

    Example:
        cik = lookup_cik_from_ticker("AAPL")
        print(cik)  # → "0000320193"
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
    Performs row-wise, order-preserving zip matching between grouped current and prior period DataFrames.

    This function groups both input DataFrames using the same set of match keys, then aligns records
    group-by-group in sequence. For each group, it zips the rows (up to the length of the shorter group)
    and returns a merged DataFrame with prefixed column names for comparison and modeling.

    Args:
        df_curr (pandas.DataFrame): Current period data, grouped on match_keys.
        df_prior (pandas.DataFrame): Prior period data, grouped on match_keys.
        match_keys (list of str): Columns to group on for alignment (e.g., ["tag", "axis_segment"]).

    Returns:
        pandas.DataFrame: Combined and aligned DataFrame with prefixed columns:
            - 'current_*': Columns from df_curr
            - 'prior_*': Columns from df_prior

    Notes:
        - If a group is missing from df_prior, it is skipped entirely.
        - Only matches rows in the same order within a group (assumes sorting or natural pairing).
        - Used for value roll-forward and period-to-period comparison logic.

    Example:
        matched = zip_match_in_order(curr_df, prior_df, match_keys=["tag", "axis_geo"])
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
    Audits a matched DataFrame for non-unique value collisions between current and prior period values.

    This function identifies cases where a single prior value maps to multiple distinct current values,
    or vice versa—indicating potential data duplication, drift, or incorrect matching. It returns a filtered
    DataFrame of all flagged collision rows for review or exclusion.

    Args:
        df (pandas.DataFrame): DataFrame containing matched period values, with:
            - 'prior_period_value'
            - 'current_period_value'

    Returns:
        pandas.DataFrame: Subset of rows where:
            - A prior value is used in >1 unique current match
            - A current value is used in >1 unique prior match

    Notes:
        - Only considers non-null numeric values.
        - Sorts the result by ['tag', 'current_period_value', 'prior_period_value'] for clarity.
        - Useful for validating matching logic and ensuring one-to-one alignment in time series data.

    Example:
        flagged = audit_value_collisions(matched_df)
        if not flagged.empty:
            print("⚠️ Review flagged rows for duplicate or overlapping matches.")
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
    Iteratively reduces match keys to find the minimal set that yields sufficient shared group overlap
    between current and prior DataFrames for zip-aligned matching.

    This function evaluates the number of shared grouped keys between current and prior data and
    progressively drops the last (least important) match key until:
        - At least 5% of current keys overlap with prior keys, or
        - The number of match keys reaches the minimum allowed.

    Args:
        curr_df (pandas.DataFrame): Current period data to group and align.
        prior_df (pandas.DataFrame): Prior period data to group and align.
        match_keys (list of str): Initial list of match keys to try (e.g., ["tag", "axis_geo", "axis_segment"]).
        min_keys (list of str): Minimum subset of match keys allowed before stopping.

    Returns:
        list of str: Final reduced list of match keys used for alignment.

    Notes:
        - Prints diagnostic info on overlap at each iteration.
        - Designed to be used before zip-style matching to avoid over-constrained groupings.
        - Matching quality improves if match_keys are ordered from most to least important.

    Example:
        best_keys = run_adaptive_match_keys(curr_df, prior_df, match_keys=["tag", "axis_geo", "axis_segment"], min_keys=["tag"])
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
    Standardizes the structure of a matched DataFrame by renaming columns, ensuring required output fields,
    and reordering columns for consistency.

    This function:
        - Renames columns using `PREFIX_MAP` (e.g., "current_tag" → "tag")
        - Ensures all `FINAL_COLS` are present, filling with None if missing
        - Preserves any extra columns and appends them after the standardized fields
        - Returns a clean, export-ready DataFrame for downstream analysis or model input

    Args:
        df (pandas.DataFrame): DataFrame containing zip-matched rows with current_*/prior_* column prefixes.

    Returns:
        pandas.DataFrame: Standardized DataFrame with:
            - All required `FINAL_COLS` in order
            - Renamed columns using `PREFIX_MAP`
            - Any extra columns preserved at the end

    Notes:
        - Relies on global constants: `PREFIX_MAP` and `FINAL_COLS`.
        - Safe to use even if some columns are missing — missing fields will be added as empty.

    Example:
        df_standardized = standardize_zip_output(matched_df)
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
    Safely parses a date string or datetime-like object into a `datetime.date` object.

    This function handles ISO-style (YYYY-MM-DD), slash-separated (MM/DD/YYYY), and pre-parsed
    `datetime.date` objects. It returns None for invalid or unrecognized inputs.

    Args:
        date_input (str or datetime-like): Input date as a string or datetime object.

    Returns:
        datetime.date or None: Parsed date object if successful; None if parsing fails.

    Notes:
        - Uses `dateutil.parser.parse()` as primary parser.
        - Falls back to manual "%m/%d/%Y" parsing for common U.S. formats.
        - Logs a warning if the input cannot be parsed.

    Example:
        parse_date("2023-06-30")    → datetime.date(2023, 6, 30)
        parse_date("06/30/2023")    → datetime.date(2023, 6, 30)
        parse_date(datetime.date(2022, 12, 31))  → datetime.date(2022, 12, 31)
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




