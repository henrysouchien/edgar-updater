"""Tool wrappers for EDGAR pipeline."""

from collections import defaultdict

import requests

from config import HEADERS, N_10K, N_10Q
from edgar_pipeline import run_edgar_pipeline
from utils import lookup_cik_from_ticker, parse_date


# NOTE: XBRL tag names are not standardized across companies. The same concept
# (e.g. revenue) can appear under different tags depending on the filer.
# This alias map covers common cases but is NOT exhaustive. Tags in EDGAR data
# are namespace-prefixed (e.g. "us-gaap:Revenues") - the lookup strips prefixes
# automatically. To support a new metric, find the actual tag(s) used by target
# companies and add them here. First tag in each list is preferred.
# TODO: Expand aliases as we identify key metrics across more companies.
# NOTE: "gross_profit" returns dollar amounts. Some companies (e.g., AAPL) label this
# "Gross margin" in filings, but we don't include that alias to avoid confusion with
# the percentage-based gross margin ratio (gross profit / revenue).
METRIC_ALIASES = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Operating revenues",
        "Total revenues",
        "Net revenues",
        "Total net sales",
        "Net sales",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
        "Net income",
        "Net income (loss)",
        "Net earnings",
    ],
    "eps": [
        "EarningsPerShareDiluted",
        "EarningsPerShareBasic",
        "Diluted EPS",
        "Diluted earnings per share",
        "Earnings per diluted share",
        "Earnings per share, diluted",
    ],
    "gross_profit": ["GrossProfit", "Gross profit"],
    "operating_income": [
        "OperatingIncomeLoss",
        "OperatingIncome",
        "Operating income",
        "Income from operations",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "Cash and cash equivalents",
        "Cash",
    ],
    "total_assets": ["Total assets", "Assets"],
    "total_liabilities": [
        "Liabilities",
        "Total liabilities",
    ],
    "total_debt": [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "DebtCurrent",
        "Total debt",
        "Long-term debt",
    ],
}


def fetch_recent_10q_10k_accessions(cik: str, headers: dict):
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    data = r.json()

    filings = data["filings"]["recent"]

    required_keys = ["form", "accessionNumber", "reportDate"]
    if not all(k in filings for k in required_keys):
        raise ValueError(
            "SEC filings JSON missing expected fields (form, accessionNumber, reportDate)."
        )

    forms = filings["form"]
    accessions = filings["accessionNumber"]
    report_dates = filings["reportDate"]
    filing_dates = filings.get("filingDate", [None] * len(forms))

    accessions_10q = []
    accessions_10k = []

    for i, form in enumerate(forms):
        entry = {
            "accession": accessions[i],
            "report_date": report_dates[i],
            "filing_date": filing_dates[i],
            "form": form,
        }

        if form == "10-Q":
            accessions_10q.append(entry)
        elif form == "10-K":
            accessions_10k.append(entry)

    return accessions_10q, accessions_10k


def filter_filings_by_year(accessions: list, max_year: int, n_limit: int):
    filtered = []
    for entry in accessions:
        date_str = entry.get("report_date", "")
        if not date_str or date_str.strip() == "":
            continue
        try:
            yr = int(date_str.split("-")[0])
        except Exception:
            continue
        if yr > max_year:
            continue
        filtered.append(entry)
        if len(filtered) >= n_limit:
            break
    return filtered


def label_10q_accessions(accessions_10q: list, accessions_10k: list):
    fiscal_year_ends = []

    for entry in accessions_10k:
        fy_date = parse_date(entry.get("report_date"))
        if fy_date:
            fiscal_year_ends.append(fy_date)

    fiscal_year_ends = sorted(fiscal_year_ends, reverse=True)

    if not fiscal_year_ends:
        raise ValueError("No valid fiscal year-end dates found in 10-Ks.")

    for q in accessions_10q:
        q_date = parse_date(q.get("report_date"))
        if not q_date:
            q["quarter"] = None
            q["label"] = None
            continue

        candidates = [fy for fy in fiscal_year_ends if fy >= q_date]
        if candidates:
            matched_fy = min(candidates)
            used_fallback = False
        else:
            candidates = [fy for fy in fiscal_year_ends if fy < q_date]
            matched_fy = max(candidates) if candidates else None
            used_fallback = True

        if matched_fy and used_fallback:
            matched_fy = matched_fy.replace(year=matched_fy.year + 1)

        if not matched_fy:
            q["quarter"] = None
            q["label"] = None
            continue

        days_diff = (matched_fy - q_date).days

        if 70 <= days_diff <= 120:
            quarter = "Q3"
        elif 160 <= days_diff <= 200:
            quarter = "Q2"
        elif 250 <= days_diff <= 300:
            quarter = "Q1"
        else:
            q["quarter"] = None
            q["label"] = None
            q["non_standard_period"] = True
            continue

        q["fiscal_year_end"] = matched_fy
        q["quarter"] = quarter
        q["calendar_year"] = q_date.year
        q["label"] = f"{quarter[1:]}Q{str(matched_fy.year)[-2:]}"

    return accessions_10q


def enrich_10k_accessions_with_fiscal_year(accessions_10k: list):
    for k in accessions_10k:
        period_end = k.get("report_date")
        dt = parse_date(period_end)
        if dt:
            k["year"] = dt.year
            k["fiscal_year_end"] = dt
        else:
            k["year"] = None
            k["fiscal_year_end"] = None
    return accessions_10k


def _dedup_facts(facts: list, pick_best_fn) -> list:
    """Deduplicate facts by (tag, date_type), keeping the best from each group."""
    groups = defaultdict(list)
    for f in facts:
        key = (f.get("tag"), f.get("date_type"))
        groups[key].append(f)
    # Preserve original ordering by using first-seen key order
    seen = []
    seen_keys = set()
    for f in facts:
        key = (f.get("tag"), f.get("date_type"))
        if key not in seen_keys:
            seen_keys.add(key)
            seen.append(key)
    return [pick_best_fn(groups[key]) for key in seen]


def build_filing_url(cik: str, accession: str) -> str:
    acc_nodash = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{acc_nodash}/{accession}-index.html"
    )


def get_filings(ticker: str, year: int, quarter: int) -> dict:
    """
    Fetch SEC filing metadata for a company/period.
    """
    cik = lookup_cik_from_ticker(ticker)
    if not cik:
        return {"status": "error", "message": f"Could not find CIK for {ticker}"}

    accessions_10q, accessions_10k = fetch_recent_10q_10k_accessions(cik, HEADERS)

    accessions_10q = filter_filings_by_year(accessions_10q, year, N_10Q)
    accessions_10k = filter_filings_by_year(accessions_10k, year, N_10K)

    accessions_10q = label_10q_accessions(accessions_10q, accessions_10k)
    accessions_10k = enrich_10k_accessions_with_fiscal_year(accessions_10k)

    filings = []
    target_label = f"{quarter}Q{str(year)[-2:]}"

    if quarter == 4:
        for k in accessions_10k:
            if k.get("year") == year:
                filings.append(
                    {
                        "form": "10-K",
                        "accession": k.get("accession"),
                        "filing_date": k.get("filing_date"),
                        "period_end": k.get("report_date"),
                        "fiscal_quarter": 4,
                        "fiscal_year": k.get("year"),
                        "url": build_filing_url(cik, k.get("accession")),
                    }
                )
    else:
        for q in accessions_10q:
            if q.get("label") == target_label:
                filings.append(
                    {
                        "form": "10-Q",
                        "accession": q.get("accession"),
                        "filing_date": q.get("filing_date"),
                        "period_end": q.get("report_date"),
                        "fiscal_quarter": int(q.get("quarter")[1:]) if q.get("quarter") else None,
                        "fiscal_year": q.get("fiscal_year_end").year
                        if q.get("fiscal_year_end")
                        else None,
                        "url": build_filing_url(cik, q.get("accession")),
                    }
                )

    # Also check for an Item 2.02 8-K earnings release for this period
    # metadata_only=True skips the exhibit HTML download for this metadata endpoint.
    # TODO: This means exhibit_url is omitted from the 8-K entry, and period_end
    # is the expected date (not validated against exhibit content). Consider a
    # lightweight index.json fetch to get the exhibit filename without downloading HTML.
    from edgar_8k import find_8k_for_period

    entry, _html, exhibit_url, period_end_str = find_8k_for_period(
        cik, HEADERS, year, quarter, metadata_only=True
    )
    if entry:
        filing_entry = {
            "form": "8-K",
            "accession": entry.get("accession"),
            "filing_date": entry.get("filing_date"),
            "period_end": period_end_str,
            "fiscal_quarter": quarter,
            "fiscal_year": year,
            "url": build_filing_url(cik, entry.get("accession")),
            "items": entry.get("items"),
        }
        if exhibit_url:
            filing_entry["exhibit_url"] = exhibit_url
        filings.append(filing_entry)

    return {
        "status": "success",
        "ticker": ticker,
        "year": year,
        "quarter": quarter,
        "filings": filings,
    }


def get_financials(
    ticker: str,
    year: int,
    quarter: int,
    full_year_mode: bool = False,
    source: str = "auto",
) -> dict:
    """
    Extract all financial facts from SEC filings.
    """
    # Normalize source param: accept "8K", "8-K", "8k", etc.
    if source:
        source = source.strip().lower().replace("-", "")

    # Explicit 8-K request -- skip pipeline entirely
    if source == "8k":
        from edgar_8k import get_financials_from_8k

        return get_financials_from_8k(ticker, year, quarter, full_year_mode)

    # Existing pipeline call (unchanged)
    result = run_edgar_pipeline(
        ticker=ticker,
        year=year,
        quarter=quarter,
        full_year_mode=full_year_mode,
        debug_mode=False,
        excel_file=None,
        sheet_name=None,
        return_json=True,
    )

    if result.get("status") == "success":
        if "metadata" not in result:
            result["metadata"] = {}
        metadata = result["metadata"]
        if "total_facts" not in metadata:
            metadata["total_facts"] = len(result.get("facts", []))
        if "source" not in metadata:
            metadata["source"] = {
                "filing_type": "unknown",
                "period_end": "unknown",
                "url": None,
            }
        return result

    # Automatic fallback -- ONLY for "no filing found" type errors
    error_msg = result.get("message", "").lower()
    if "not found" in error_msg or "no filing" in error_msg or "no facts" in error_msg:
        from edgar_8k import get_financials_from_8k

        return get_financials_from_8k(ticker, year, quarter, full_year_mode)

    return result


def get_metric_from_result(
    result: dict,
    metric_name: str,
    ticker: str,
    year: int,
    quarter: int,
    full_year_mode: bool = False,
    date_type=None,
) -> dict:
    if result.get("status") != "success":
        return result

    if not metric_name:
        return {"status": "error", "message": "Metric name is required"}

    search_tags = METRIC_ALIASES.get(metric_name.lower(), [metric_name])

    def has_value(fact: dict) -> bool:
        return (
            fact.get("current_period_value") is not None
            or fact.get("visual_current_value") is not None
        )

    def is_consolidated(fact: dict) -> bool:
        seg = fact.get("segment")
        if seg is None:
            seg = fact.get("axis_segment")
        return seg is None or seg == "" or seg == {}

    def pick_best_fact(fact_list: list) -> dict:
        if len(fact_list) == 1:
            return fact_list[0]
        for fact in fact_list:
            if is_consolidated(fact) and has_value(fact):
                return fact
        for fact in fact_list:
            if has_value(fact):
                return fact
        return fact_list[0]

    def find_all_matching_facts(facts: list, tags: list):
        """Return all facts matching any alias tag across tiers.

        Stops at the first tier that produces matches (higher tiers are
        more precise). Within a tier, collects all matching facts across
        all aliases. Deduplicates by (tag, date_type), picking the best
        fact (consolidated, with value) from each group.
        """
        import re

        facts_by_tag = defaultdict(list)
        for fact in facts:
            raw_tag = fact.get("tag") or ""  # Guard against None
            facts_by_tag[raw_tag].append(fact)
            # Also index by bare name (strip us-gaap: or other namespace prefixes)
            if raw_tag and ":" in raw_tag:
                bare_tag = raw_tag.split(":", 1)[1]
                facts_by_tag[bare_tag].append(fact)

        def collect_tier(match_fn):
            """Collect all facts matching any alias via match_fn."""
            matched = []
            seen = set()
            for tag in tags:
                for fact_tag, fact_list in facts_by_tag.items():
                    if match_fn(tag, fact_tag):
                        for f in fact_list:
                            fid = id(f)
                            if fid not in seen:
                                seen.add(fid)
                                matched.append(f)
            return matched

        # Tier 1: exact match
        tier1 = []
        seen = set()
        for tag in tags:
            if tag in facts_by_tag:
                for f in facts_by_tag[tag]:
                    fid = id(f)
                    if fid not in seen:
                        seen.add(fid)
                        tier1.append(f)
        if tier1:
            return _dedup_facts(tier1, pick_best_fact)

        # Tier 2: prefix match
        tier2 = collect_tier(lambda tag, ft: ft.startswith(tag))
        if tier2:
            return _dedup_facts(tier2, pick_best_fact)

        # Tier 3: whole-word match (for 8-K raw labels)
        patterns = {tag: re.compile(r"\b" + re.escape(tag) + r"\b", re.IGNORECASE) for tag in tags}
        tier3 = collect_tier(lambda tag, ft: patterns[tag].search(ft))
        if tier3:
            return _dedup_facts(tier3, pick_best_fact)

        # Tier 4: substring match (last resort)
        tier4 = collect_tier(lambda tag, ft: tag.lower() in ft.lower())
        if tier4:
            return _dedup_facts(tier4, pick_best_fact)

        return []

    facts = result.get("facts", [])
    if not facts:
        return {
            "status": "error",
            "message": f"No facts found in {ticker} Q{quarter} {year} filing",
        }

    # Normalize date_type input (API callers may send lowercase)
    if date_type and isinstance(date_type, str):
        date_type = date_type.upper().strip()
        if date_type not in ("Q", "YTD", "FY"):
            date_type = None

    # Search all facts for matches, then filter by date_type after
    all_matches = find_all_matching_facts(facts, search_tags)

    # Apply date_type filter to matches
    #
    # TODO: Known issues with date_type filtering and balance sheet items:
    #
    # 1. Balance sheet (instant/point-in-time) items are always labeled "Q" in
    #    the 8-K path and None in the iXBRL pipeline — they never have "FY".
    #    When full_year_mode=True, BS metrics survive only because the FY/YTD
    #    filter finds nothing and falls through. This works but is implicit.
    #
    # 2. When full_year_mode=False and date_type=None, NO filtering is applied,
    #    so all date_types (Q, FY, YTD) are returned in the matches list.
    #    Callers must pick the right one from the list.
    #
    # 3. The "source" param on get_financials is currently just an 8-K override
    #    flag ("8k" vs everything else). There's no source="10k" or "10q" —
    #    the pipeline selects 10-Q vs 10-K internally based on quarter/year.
    #
    if date_type:
        all_matches = [f for f in all_matches if f.get("date_type") == date_type]
    elif full_year_mode:
        fy_matches = [f for f in all_matches if f.get("date_type") == "FY"]
        if not fy_matches:
            fy_matches = [f for f in all_matches if f.get("date_type") == "YTD"]
        if fy_matches:
            all_matches = fy_matches

    if not all_matches:
        return {
            "status": "error",
            "message": (
                f"Metric '{metric_name}' not found in {ticker} Q{quarter} {year} filing"
            ),
        }

    period = f"FY {year}" if full_year_mode else f"Q{quarter} {year}"

    matches = []
    for fact in all_matches:
        current = fact.get("visual_current_value")
        if current is None:
            current = fact.get("current_period_value")
        prior = fact.get("visual_prior_value")
        if prior is None:
            prior = fact.get("prior_period_value")

        yoy_change = None
        yoy_pct = None
        if current is not None and prior is not None and prior != 0:
            yoy_change = current - prior
            yoy_pct = round((yoy_change / abs(prior)) * 100, 1)

        matches.append({
            "metric": fact.get("tag"),
            "current_value": current,
            "prior_value": prior,
            "yoy_change": yoy_change,
            "yoy_change_pct": f"{yoy_pct}%" if yoy_pct is not None else None,
            "date_type": fact.get("date_type"),
        })

    return {
        "status": "success",
        "ticker": ticker,
        "period": period,
        "matches": matches,
        "source": result.get("metadata", {}).get("source", {}),
    }


def get_metric(
    ticker: str,
    year: int,
    quarter: int,
    metric_name: str,
    full_year_mode: bool = False,
    source: str = "auto",
    date_type=None,
) -> dict:
    """
    Get a specific financial metric.
    """
    result = get_financials(ticker, year, quarter, full_year_mode, source=source)
    return get_metric_from_result(
        result,
        metric_name,
        ticker,
        year,
        quarter,
        full_year_mode,
        date_type=date_type,
    )
