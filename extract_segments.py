import json
import sys

# Read the file directly
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
