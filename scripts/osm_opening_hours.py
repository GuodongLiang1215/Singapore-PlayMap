"""Opening hours from OpenStreetMap (OSM) for catalogue places that have none.

Step 1 - download (needs internet, 1-3 minutes):
    .\\.venv\\Scripts\\python.exe scripts\\osm_opening_hours.py fetch
Step 2 - check coverage only (changes nothing):
    .\\.venv\\Scripts\\python.exe scripts\\osm_opening_hours.py report

Data: OSM places in Singapore that have both a name and an "opening_hours" tag.
Licence: ODbL. Attribution required: "© OpenStreetMap contributors".
Matching: same or very similar name, within 60 m (200 m for malls, whose OSM point is
the building centre). OSM hours are community edited and can be out of date, so they
are shown as reference times, never as verified opening hours.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.stage1.normalise import normal_name  # noqa: E402

RAW = ROOT / 'data' / 'raw' / 'osm' / 'osm_opening_hours.json'
REPORT = ROOT / 'review' / 'osm_opening_hours_report.json'
ENDPOINTS = ['https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter']
QUERY = """[out:json][timeout:240];
area["ISO3166-1"="SG"][admin_level=2]->.sg;
nwr["opening_hours"]["name"](area.sg);
out center tags;"""
NEAR_M, MALL_M = 60.0, 200.0
GRID = 0.002  # about 220 m


def metres(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, h))))


def cmd_fetch(_args):
    body = urllib.parse.urlencode({'data': QUERY}).encode()
    last = None
    for url in ENDPOINTS:
        try:
            print(f'Downloading from {url} (1-3 minutes)...')
            req = urllib.request.Request(url, data=body, headers={'User-Agent': 'SingaporePlayMap-student-project/1.0'})
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.load(r)
            break
        except Exception as exc:  # try the next mirror
            last = exc
            print(f'  failed: {exc}')
    else:
        sys.exit(f'Could not download from Overpass: {last}')
    places = []
    for el in data.get('elements', []):
        tags = el.get('tags', {})
        lat = el.get('lat', (el.get('center') or {}).get('lat'))
        lon = el.get('lon', (el.get('center') or {}).get('lon'))
        if lat is None or not tags.get('opening_hours') or not tags.get('name'):
            continue
        places.append({'osm_id': f"{el['type']}/{el['id']}", 'lat': lat, 'lon': lon,
                       'name': tags.get('name'), 'name_en': tags.get('name:en'), 'brand': tags.get('brand'),
                       'branch': tags.get('branch'), 'opening_hours': tags['opening_hours'],
                       'kind': tags.get('shop') or tags.get('amenity') or tags.get('tourism') or tags.get('leisure')})
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps({'retrieved_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                               'licence': 'ODbL; © OpenStreetMap contributors', 'query': QUERY,
                               'count': len(places), 'places': places}, ensure_ascii=False), encoding='utf-8')
    print(f'Saved {len(places)} OSM places with opening hours to {RAW.relative_to(ROOT)}')
    print(f'Kinds: {dict(Counter(p["kind"] for p in places).most_common(15))}')


def names_of(p):
    out = {normal_name(p.get(k)) for k in ('name', 'name_en', 'brand') if p.get(k)}
    if p.get('brand') and p.get('branch'):
        out.add(normal_name(f"{p['brand']} {p['branch']}"))
    return out - {''}


NOT_HOURS = {'off', 'closed', 'no', 'unknown', 'n/a', 'na', '-', ''}


def usable_hours(value) -> bool:
    """OSM uses "off" / "closed" for places that are not open (e.g. under construction)."""
    return str(value or '').strip().lower() not in NOT_HOURS


def score(a: str, b: str, allow_prefix: bool = True) -> float:
    if a == b:
        return 1.0
    short, long_ = sorted((a, b), key=len)
    # Only "brand" vs "brand + branch" ("starbucks" / "starbucks ion orchard"). A shop called
    # "tonkatsu enbiton vivocity" must not give its hours to the mall "vivocity".
    if allow_prefix and len(short) >= 5 and long_.startswith(short + ' '):
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def load_catalogue():
    ptr = json.loads((ROOT / 'data' / 'processed' / 'stage1c_current.json').read_text(encoding='utf-8'))
    db = sqlite3.connect((ROOT / ptr['database_relative_path']).resolve().as_uri() + '?mode=ro', uri=True)
    rows = [json.loads(r[0]) for r in db.execute('SELECT data_json FROM entities WHERE visible=1 AND map_eligible=1')]
    db.close()
    return ptr['build_id'], rows


def match_all(osm, entities):
    grid = defaultdict(list)
    for p in osm:
        if not usable_hours(p.get('opening_hours')):
            continue
        grid[(int(p['lat'] // GRID), int(p['lon'] // GRID))].append((p, names_of(p)))
    matches = {}
    for e in entities:
        if e.get('opening_hours_display'):
            continue  # reviewed core attractions keep their own hours
        name = normal_name(e['display_title'])
        rule = (e.get('suggested_duration') or {}).get('rule')
        limit = MALL_M if rule == 'mall' else NEAR_M
        ci, cj = int(e['latitude'] // GRID), int(e['longitude'] // GRID)
        best = None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for p, pnames in grid.get((ci + di, cj + dj), []):
                    d = metres(e['latitude'], e['longitude'], p['lat'], p['lon'])
                    if d > limit:
                        continue
                    # "brand" vs "brand + branch" only for chains tagged with a brand in OSM, so that
                    # "Lorong Melayu Park" does not take the hours of "Lorong Melayu Park Fitness Centre".
                    s = max(score(name, n, allow_prefix=bool(p.get('brand'))) for n in pnames)
                    if s >= 0.86 and (best is None or (s, -d) > (best[0], -best[1])):
                        best = (s, d, p)
        if best:
            matches[e['entity_id']] = {'osm_id': best[2]['osm_id'], 'osm_name': best[2]['name'],
                                       'opening_hours': best[2]['opening_hours'],
                                       'name_score': round(best[0], 3), 'distance_m': round(best[1], 1)}
    # One OSM record gives hours to one place only: the best name match, then the nearest.
    best_for = {}
    for eid, m in matches.items():
        cur = best_for.get(m['osm_id'])
        if cur is None or (m['name_score'], -m['distance_m']) > (matches[cur]['name_score'], -matches[cur]['distance_m']):
            best_for[m['osm_id']] = eid
    keep = set(best_for.values())
    return {eid: m for eid, m in matches.items() if eid in keep}


def cmd_report(_args):
    if not RAW.is_file():
        sys.exit('Run "fetch" first.')
    osm = json.loads(RAW.read_text(encoding='utf-8'))
    build_id, entities = load_catalogue()
    matches = match_all(osm['places'], entities)
    by_rule_total, by_rule_hit = Counter(), Counter()
    for e in entities:
        if e.get('opening_hours_display'):
            continue
        rule = (e.get('suggested_duration') or {}).get('rule') or 'other'
        by_rule_total[rule] += 1
        if e['entity_id'] in matches:
            by_rule_hit[rule] += 1
    print(f'OSM places with hours: {osm["count"]} (downloaded {osm["retrieved_at"]})')
    print(f'Catalogue places without hours: {sum(by_rule_total.values())}; matched to OSM: {len(matches)}\n')
    print(f'{"category rule":<16}{"matched":>9}{"total":>8}{"share":>8}')
    for rule, total in by_rule_total.most_common():
        hit = by_rule_hit[rule]
        print(f'{rule:<16}{hit:>9}{total:>8}{hit / total:>8.0%}')
    titles = {e['entity_id']: e['display_title'] for e in entities}
    sample = [dict(m, place=titles[k]) for k, m in list(matches.items())[:40]]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'catalogue_build': build_id, 'matched': len(matches),
                                  'by_rule': {r: [by_rule_hit[r], t] for r, t in by_rule_total.items()},
                                  'sample': sample}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\nSample matches saved to {REPORT.relative_to(ROOT)} (nothing in the catalogue was changed).')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('fetch', help='download OSM opening hours for Singapore')
    sub.add_parser('report', help='count how many catalogue places can be matched (no changes)')
    args = ap.parse_args()
    {'fetch': cmd_fetch, 'report': cmd_report}[args.cmd](args)


if __name__ == '__main__':
    main()
