"""Default visit durations by detailed category (config/duration_defaults.csv).

"外带（分钟）" is optional: only categories where people often just buy and leave (cafe, bubble tea,
fast food, small shops) have it.
Rules are checked top to bottom; the first rule whose pattern matches wins.
Patterns: Overture sub-categories (with * wildcards), "source:<source_key>" for official
datasets, and "nparks:small" / "nparks:nature" for NParks names.
Reviewed core attractions (config/core_attractions.json) override these defaults.
"""
from __future__ import annotations

import csv
from fnmatch import fnmatchcase
from pathlib import Path

SMALL_WORDS = ('PLAYGROUND', ' PG', 'FITNESS CORNER', ' FC', 'OPEN SPACE', ' OS', 'INTERIM')
NATURE_WORDS = ('NATURE RESERVE', 'NATURE PARK', 'WETLAND', 'MARSHES', 'CATCHMENT', 'PULAU ', 'MARINE PARK')


def load_rules(path: Path):
    """Read the comparison table by column name, so columns can be reordered in Excel."""
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    rules = []
    for r in rows:
        rid = (r.get('规则') or '').strip()
        if not rid:
            continue
        short, regular, long_ = int(r['短（分钟）']), int(r['一般（分钟）']), int(r['长（分钟）'])
        if not 0 < short <= regular <= long_ <= 720:
            raise ValueError(f'duration_defaults.csv rule {rid}: need 0 < short <= regular <= long <= 720')
        takeaway = (r.get('外带（分钟）') or '').strip()
        takeaway = int(takeaway) if takeaway else None
        if takeaway is not None and not 0 < takeaway <= short:
            raise ValueError(f'duration_defaults.csv rule {rid}: takeaway must be between 1 and the short value')
        patterns = [p.strip() for p in (r.get('匹配类别') or '').split(';') if p.strip()]
        rules.append({'rule': rid, 'label': (r.get('名称') or '').strip(), 'patterns': patterns,
                      'takeaway': takeaway, 'short': short, 'regular': regular, 'long': long_})
    return rules


def keys_for(entity: dict) -> list[str]:
    if entity.get('data_source') == 'overture':
        sub = (entity.get('overture') or {}).get('category')
        return [sub] if sub else []
    source = str(entity.get('primary_uid', '')).split(':')[0]
    keys = []
    if source == 'nparks_parks':
        name = ' ' + str(entity.get('source_title') or entity.get('display_title') or '').upper()
        if any(w in name for w in NATURE_WORDS):
            keys.append('nparks:nature')
        elif any(w in name for w in SMALL_WORDS):
            keys.append('nparks:small')
    keys.append('source:' + source)
    return keys


def match(rules, entity: dict):
    for key in keys_for(entity):
        for rule in rules:
            if any(fnmatchcase(key, p) for p in rule['patterns']):
                return rule
    return None
