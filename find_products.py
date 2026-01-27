"""
find_products.py - Attempt to find product-level revenue breakdown

STATUS: Experimental / Needs Refactoring
CREATED: During agent workflow testing (Jan 2026)

This script was created to investigate whether product segments (iPhone, Mac, etc.)
are available in the XBRL data. Finding: Product segments are NOT reliably available
in standard XBRL - only geographic segments are consistently reported.

Currently has hardcoded file path from a specific test run.

TODO:
- Remove hardcoded file path
- Accept input via stdin or command line argument
- Document finding: product segments require custom XBRL tags (aapl:*)
- Consider removing if product segments remain unavailable
- Or repurpose to detect company-specific custom tags

NOTE: This script revealed that geographic segments (axis_segment) work well,
but product breakdowns require parsing company-specific extensions.
"""

import json

# Read the file directly
# TODO: Change to stdin or parameterized input
with open('/Users/henrychien/.claude/projects/-Users-henrychien-Documents-Jupyter-Edgar-updater/e98a92be-09b5-4ceb-ae6c-6d536e0f9f29/tool-results/toolu_019HzU8qmi7GWkQdQ5p8L7Lq.txt') as f:
    content = f.read()
    data = json.loads(content.strip().split('\n')[0])

# Q3 revenue facts with products/services role
products_role = 'condensedconsolidatedstatementsofoperationsunaudited|revenuenetsalesdisaggregatedbysignificantproductsandservicesdetails|segmentinformationandgeographicdatainformationbyreportablesegmentdetails'

q3_revenues = []
for fact in data['facts']:
    if (fact['tag'] == 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax' and
        fact.get('presentation_role') == products_role and
        fact.get('date_type') == 'Q' and
        fact.get('axis_geo') == '__NONE__' and
        fact.get('axis_segment') == '__NONE__'):

        current = fact.get('visual_current_value', 0)
        prior = fact.get('visual_prior_value', 0)
        growth = ((current - prior) / prior * 100) if prior > 0 else 0

        q3_revenues.append({
            'current': current,
            'prior': prior,
            'growth': growth,
            'fact': fact
        })

# Sort by current value descending
q3_revenues.sort(key=lambda x: x['current'], reverse=True)

print('Q3 Revenue Breakdown (sorted by size):')
print('=' * 80)

# Based on Apple's typical structure and the values:
# 1. Total: 85,777
# 2. Products total: 61,564
# 3. iPhone: 39,296 (largest product)
# 4. Services: 24,213 (growing +14%)
# 5. Mac: 7,009 or 8,097
# 6. iPad: 7,162 or 8,097 or 7,009
# 7. Wearables: remaining

labels = ['Total Revenue', 'Products Total', 'iPhone', 'Services', 'Product 5', 'Product 6', 'Product 7']

for i, rev in enumerate(q3_revenues):
    label = labels[i] if i < len(labels) else f'Unknown {i}'
    print(f'\n{label}:')
    print(f'  Q3 2024: ${rev["current"]:,.0f}M')
    print(f'  Q3 2023: ${rev["prior"]:,.0f}M')
    print(f'  YoY Growth: {rev["growth"]:+.2f}%')

# Try to find product labels in the data
print('\n\n' + '=' * 80)
print('Searching for product category labels...')
print('=' * 80)

# Check if there are any custom tags
custom_tags = set()
for fact in data['facts']:
    tag = fact.get('tag', '')
    if 'aapl:' in tag.lower():
        custom_tags.add(tag)

if custom_tags:
    print('\nApple-specific tags:')
    for tag in sorted(custom_tags):
        print(f'  {tag}')
