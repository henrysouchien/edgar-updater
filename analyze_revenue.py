"""
analyze_revenue.py - Revenue analysis helper script

STATUS: Experimental / Needs Refactoring
CREATED: During agent workflow testing (Jan 2026)

This script was created to help analyze API response data during testing.
It parses JSON from stdin and extracts revenue by segment and geography.

TODO:
- Abstract into reusable functions (e.g., get_metric_by_segment())
- Remove hardcoded values (80000 threshold, 1000 filter)
- Accept ticker/quarter as parameters instead of hardcoded "Q3 2024"
- Could be integrated into a utils/analysis.py module

USAGE (current):
    curl "API_URL" | python analyze_revenue.py

USAGE (future):
    from analysis import analyze_revenue
    analyze_revenue(data, metric="Revenue", breakdown=["segment", "geo"])
"""

import json
import sys

content = sys.stdin.read()
data = json.loads(content.strip().split('\n')[0])

print('Apple Q3 2024 Revenue Analysis')
print('=' * 60)

# Get total revenue
for fact in data['facts']:
    if fact['tag'] == 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax':
        if fact.get('date_type') == 'Q' and fact.get('axis_geo') == '__NONE__' and fact.get('axis_segment') == '__NONE__':
            current = fact.get('visual_current_value', 0)
            prior = fact.get('visual_prior_value', 0)
            if current > 80000:
                print(f'\nTotal Revenue:')
                print(f'  Q3 2024: ${current:,.0f}M')
                print(f'  Q3 2023: ${prior:,.0f}M')
                growth = ((current - prior) / prior) * 100
                print(f'  YoY Growth: {growth:.2f}%')
                break

# Get product segments
print('\n\nProduct Segment Revenue:')
print('-' * 60)

segments = {}
for fact in data['facts']:
    if fact['tag'] == 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax':
        if fact.get('date_type') == 'Q' and fact.get('axis_geo') == '__NONE__':
            segment = fact.get('axis_segment', '')
            if segment and segment != '__NONE__':
                current = fact.get('visual_current_value', 0)
                prior = fact.get('visual_prior_value', 0)
                if current > 1000:
                    segments[segment] = {
                        'current': current,
                        'prior': prior,
                        'growth': ((current - prior) / prior * 100) if prior > 0 else 0
                    }

# Sort by current value descending
for seg_name in sorted(segments.keys(), key=lambda x: segments[x]['current'], reverse=True):
    seg = segments[seg_name]
    print(f'\n{seg_name}:')
    print(f'  Q3 2024: ${seg["current"]:,.0f}M')
    print(f'  Q3 2023: ${seg["prior"]:,.0f}M')
    print(f'  YoY Growth: {seg["growth"]:+.2f}%')

# Geographic segments
print('\n\nGeographic Revenue:')
print('-' * 60)

geos = {}
for fact in data['facts']:
    if fact['tag'] == 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax':
        if fact.get('date_type') == 'Q' and fact.get('axis_segment') == '__NONE__':
            geo = fact.get('axis_geo', '')
            if geo and geo != '__NONE__':
                current = fact.get('visual_current_value', 0)
                prior = fact.get('visual_prior_value', 0)
                if current > 1000:
                    geos[geo] = {
                        'current': current,
                        'prior': prior,
                        'growth': ((current - prior) / prior * 100) if prior > 0 else 0
                    }

# Sort by current value descending
for geo_name in sorted(geos.keys(), key=lambda x: geos[x]['current'], reverse=True):
    geo = geos[geo_name]
    print(f'\n{geo_name}:')
    print(f'  Q3 2024: ${geo["current"]:,.0f}M')
    print(f'  Q3 2023: ${geo["prior"]:,.0f}M')
    print(f'  YoY Growth: {geo["growth"]:+.2f}%')
