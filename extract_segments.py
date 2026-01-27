"""
extract_segments.py - Extract unique segment values from API response

STATUS: Experimental / Needs Refactoring
CREATED: During agent workflow testing (Jan 2026)

This script was created to discover what segment values exist in the API data.
Currently has hardcoded file path from a specific test run.

TODO:
- Remove hardcoded file path
- Accept input via stdin or command line argument
- Abstract into a function: get_unique_segments(data) -> list
- Could be merged into analyze_revenue.py or a utils module

USAGE (future):
    curl "API_URL" | python extract_segments.py
    # or
    from analysis import get_unique_segments
    segments = get_unique_segments(data)
"""

import json
import sys

# Read the file directly
# TODO: Change to stdin or parameterized input
with open('/Users/henrychien/.claude/projects/-Users-henrychien-Documents-Jupyter-Edgar-updater/e98a92be-09b5-4ceb-ae6c-6d536e0f9f29/tool-results/toolu_019HzU8qmi7GWkQdQ5p8L7Lq.txt') as f:
    content = f.read()
    data = json.loads(content.strip().split('\n')[0])

# Get all unique segment values
segments = set()
for fact in data['facts']:
    seg = fact.get('axis_segment', '')
    if seg and seg != '__NONE__':
        segments.add(seg)

print('All unique segment axis values:')
for seg in sorted(segments):
    print(f'  {seg}')
