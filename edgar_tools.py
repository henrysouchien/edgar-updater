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
METRIC_ALIASES = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss", "OperatingIncome"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "Cash"],
    "total_assets": ["Assets"],
    "total_debt": ["LongTermDebt", "LongTermDebtNoncurrent", "DebtCurrent"],
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

    return {
        "status": "success",
        "ticker": ticker,
        "year": year,
        "quarter": quarter,
        "filings": filings,
    }


def get_financials(ticker: str, year: int, quarter: int, full_year_mode: bool = False) -> dict:
    """
    Extract all financial facts from SEC filings.
    """
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


def get_metric(
    ticker: str,
    year: int,
    quarter: int,
    metric_name: str,
    full_year_mode: bool = False,
) -> dict:
    """
    Get a specific financial metric.
    """
    result = get_financials(ticker, year, quarter, full_year_mode)

    if result.get("status") != "success":
        return result

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

    def find_matching_fact(facts: list, tags: list):
        facts_by_tag = defaultdict(list)
        for fact in facts:
            raw_tag = fact.get("tag", "")
            facts_by_tag[raw_tag].append(fact)
            # Also index by bare name (strip us-gaap: or other namespace prefixes)
            if ":" in raw_tag:
                bare_tag = raw_tag.split(":", 1)[1]
                facts_by_tag[bare_tag].append(fact)

        for tag in tags:
            if tag in facts_by_tag:
                return pick_best_fact(facts_by_tag[tag])

        for tag in tags:
            for fact_tag, fact_list in facts_by_tag.items():
                if fact_tag.startswith(tag):
                    return pick_best_fact(fact_list)

        return None

    facts = result.get("facts", [])
    if not facts:
        return {
            "status": "error",
            "message": f"No facts found in {ticker} Q{quarter} {year} filing",
        }

    matched_fact = find_matching_fact(facts, search_tags)
    if not matched_fact:
        return {
            "status": "error",
            "message": (
                f"Metric '{metric_name}' not found in {ticker} Q{quarter} {year} filing"
            ),
        }

    current = matched_fact.get("visual_current_value")
    if current is None:
        current = matched_fact.get("current_period_value")
    prior = matched_fact.get("visual_prior_value")
    if prior is None:
        prior = matched_fact.get("prior_period_value")

    yoy_change = None
    yoy_pct = None
    if current is not None and prior is not None and prior != 0:
        yoy_change = current - prior
        yoy_pct = round((yoy_change / abs(prior)) * 100, 1)

    period = f"FY {year}" if full_year_mode else f"Q{quarter} {year}"

    return {
        "status": "success",
        "ticker": ticker,
        "metric": matched_fact.get("tag"),
        "period": period,
        "current_value": current,
        "prior_value": prior,
        "yoy_change": yoy_change,
        "yoy_change_pct": f"{yoy_pct}%" if yoy_pct is not None else None,
        "source": result.get("metadata", {}).get("source", {}),
    }
