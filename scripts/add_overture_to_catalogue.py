"""Add exported Overture places to the current Stage1C catalogue as a NEW snapshot.

Run after scripts/fetch_overture_places.py export:

    .\\.venv\\Scripts\\python.exe scripts\\add_overture_to_catalogue.py

What it does
- Reads the current Stage1C snapshot read-only. The official snapshot is never changed.
- Copies it to a new folder data/processed/stage1c/<new build id>/ and adds Overture
  places there as single-source entities, mapped to the app's existing 5 categories.
- Skips an Overture place that is probably the same as an official place
  (within 150 m and a similar name); these are listed in overture_possible_duplicates.json.
- Moves data/processed/stage1c_current.json to the new snapshot.
  To undo: python scripts/add_overture_to_catalogue.py --rollback

Running it again always starts from the official snapshot, so Overture data is
never added twice. If Stage1C is rebuilt from Stage1B, run this script again.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.stage1.build import BuildError, dump_json, read_json, safe_path  # noqa: E402
from app.stage1.normalise import normal_name, normalise_feature  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clean_names import clean_title  # noqa: E402
from duration_defaults import load_rules, match as match_duration  # noqa: E402
import osm_opening_hours  # noqa: E402

ATTRIBUTION = [
    'Overture Maps Foundation, Places theme (see https://docs.overturemaps.org/attribution)',
    '© OpenStreetMap contributors, ODbL (opening hours for some places)',
    'Singapore government open data via data.gov.sg (STB, NParks, NHB, NEA), Singapore Open Data Licence',
    'OneMap (Singapore Land Authority) for entrance coordinates of core attractions',
]

SOURCE_KEY = 'overture_places'
POINTER = ROOT / 'data' / 'processed' / 'stage1c_current.json'
STAGE1C_DIR = Path('data') / 'processed' / 'stage1c'
DUPLICATE_RADIUS_M = 150.0
NAME_SIMILARITY = 0.86
GRID = 0.002  # degrees, about 220 m; neighbour cells cover the 150 m radius

SOURCE = {
    'key': SOURCE_KEY, 'dataset_id': SOURCE_KEY, 'role': 'candidate_places',
    'catalogue_url': 'https://docs.overturemaps.org/getting-data/',
    'licence_url': 'https://docs.overturemaps.org/attribution',
    'expected_geometry_types': ['Point'],
}

TOP_LEVEL_ZH = {
    'food_and_drink': '餐饮', 'shopping': '购物', 'arts_and_entertainment': '娱乐 艺术',
    'sports_and_recreation': '运动 休闲', 'cultural_and_historic': '文化 历史',
    'geographic_entities': '自然 地标',
}
# Chinese search words for common sub-categories (searched as text, not a category)
SUB_ZH = {
    'restaurant': '餐厅', 'chinese_restaurant': '中餐 餐厅', 'japanese_restaurant': '日料 日本菜 餐厅',
    'fast_food_restaurant': '快餐', 'coffee_shop': '咖啡', 'cafe': '咖啡 咖啡馆', 'bubble_tea_shop': '奶茶',
    'singaporean_restaurant': '本地菜 新加坡菜', 'food_court': '美食广场 熟食', 'food_truck_stand': '小贩 摊位 熟食',
    'food_and_drink': '美食', 'dim_sum_restaurant': '点心 早茶', 'hong_kong_style_cafe': '茶餐厅 港式',
    'malaysian_restaurant': '马来 马来西亚菜', 'indonesian_restaurant': '印尼菜', 'seafood_restaurant': '海鲜',
    'dessert_shop': '甜品', 'ice_cream_shop': '冰淇淋', 'gelato_shop': '冰淇淋 意式冰淇淋', 'tea_room': '茶室 喝茶',
    'chocolatier': '巧克力', 'bar': '酒吧', 'cocktail_bar': '酒吧 鸡尾酒', 'wine_bar': '酒吧 红酒',
    'speakeasy': '酒吧 地下酒吧', 'brewery': '啤酒 精酿', 'beer_garden': '啤酒', 'gastropub': '酒吧 餐吧',
    'tapas_bar': '酒吧 小食', 'sake_bar': '酒吧 清酒', 'whiskey_bar': '酒吧 威士忌', 'beach_bar': '酒吧 海边',
    'theme_restaurant': '主题餐厅', 'buffet_restaurant': '自助餐', 'breakfast_and_brunch_restaurant': '早午餐 早餐',
    'shopping_mall': '商场 购物中心 购物', 'department_store': '百货 商场 购物', 'shopping': '购物',
    'gift_shop': '礼品 纪念品', 'souvenir_store': '纪念品 手信', 'bookstore': '书店', 'antique_store': '古董',
    'comic_books_store': '漫画', 'vinyl_record_store': '唱片', 'video_game_store': '游戏 电玩',
    'night_market': '夜市', 'flea_market': '跳蚤市场 市集', 'farmers_market': '市集', 'street_vendor': '小摊',
    'pop_up_store': '快闪店', 'outlet_store': '奥特莱斯 折扣', 'duty_free_store': '免税店',
    'museum': '博物馆', 'art_gallery': '美术馆 画廊 艺术', 'aquarium': '水族馆', 'zoo': '动物园',
    'theatre': '剧院', 'cinema': '电影院', 'arcade': '游戏厅 电玩', 'amusement_park': '游乐园',
    'park': '公园', 'playground': '游乐场 儿童', 'beach': '海滩', 'garden': '花园', 'botanical_garden': '植物园',
    'hiking_trail': '徒步 步道', 'historic_site': '古迹 历史', 'monument': '纪念碑 古迹',
    'religious_organization': '宗教 寺庙 教堂', 'buddhist_temple': '佛寺 寺庙', 'hindu_temple': '印度庙 寺庙',
    'mosque': '清真寺', 'church_cathedral': '教堂', 'landmark_and_historical_building': '地标 历史建筑',
}
NATURE_WORDS = ('park', 'garden', 'playground', 'beach', 'trail', 'nature', 'forest', 'lake', 'waterfall',
                'river', 'island', 'mountain', 'hill', 'reserve', 'wetland', 'reservoir')
# Sub-categories the team decided not to show (not places for a day out)
EXCLUDED_SUBCATEGORIES = {'betting_center', 'lottery_vendor', 'psychic_advising', 'astrological_advising',
                          'adult_entertainment_venue'}
# Larger SQLite pages: a ~2 KB record no longer takes a whole 4 KB page
PAGE_SIZE = 16384
CATEGORY_LABEL = {'food': '餐饮', 'tourism': '景点', 'nature': '公园/自然', 'heritage': '历史文化'}


def app_category(top: str | None, sub: str | None) -> str:
    """Map Overture categories onto the app's existing 5 categories."""
    sub = sub or ''
    if top == 'food_and_drink':
        return 'food'
    if top == 'cultural_and_historic':
        return 'heritage'
    if top in ('geographic_entities', 'sports_and_recreation') and any(w in sub for w in NATURE_WORDS):
        return 'nature'
    if top == 'geographic_entities' and not any(w in sub for w in ('quay', 'pier', 'bridge', 'marina')):
        return 'nature'
    return 'tourism'  # shopping, entertainment, sports venues, quays/bridges


def metres(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, h))))


ABBREVIATIONS = {'pg': 'playground', 'pk': 'park', 'rd': 'road', 'ave': 'avenue', 'dr': 'drive', 'jln': 'jalan',
                 'st': 'street', 'cres': 'crescent', 'ctr': 'centre', 'blk': 'block', 'lor': 'lorong',
                 'upp': 'upper', 'gdn': 'garden', 'gdns': 'gardens', 'terr': 'terrace'}
# Extra words that do not make a different place ("Hong San See" vs "Hong San See Temple")
GENERIC_WORDS = {'temple', 'museum', 'market', 'and', 'food', 'centre', 'center', 'hawker', 'historic',
                 'marker', 'singapore', 'the', 'site', 'building', 'monument'}


def expand(name: str) -> str:
    return ' '.join(ABBREVIATIONS.get(w, w) for w in name.split())


def similar(overture: str, official: str) -> float:
    """Name similarity, asymmetric on purpose.

    A shop whose name merely contains an area or park name ("Ya Kun (One Raffles Place)")
    is a different place, so containment counts only when the Overture name sits inside
    the official name, or when the extra Overture words are generic.
    """
    a, b = expand(overture), expand(official)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 8 and a in b:
        return 0.9
    if len(b) >= 8 and b in a and set(a.split()) - set(b.split()) <= GENERIC_WORDS:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def cell(lat, lon):
    return int(math.floor(lat / GRID)), int(math.floor(lon / GRID))


def j(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def current_official_snapshot():
    """Return (db path, report) of the official snapshot this run builds on."""
    ptr = read_json(POINTER)
    rp = safe_path(ROOT, ptr['report_relative_path'], STAGE1C_DIR)
    report = read_json(rp)
    base = report.get('overture_supplement', {}).get('base_build_id')
    if base:  # pointer already shows a supplemented build: go back to its official base
        rp = safe_path(ROOT, f'{STAGE1C_DIR.as_posix()}/{base}/stage1c_profile.json', STAGE1C_DIR)
        report = read_json(rp)
    if report.get('stage') != '1C' or 'overture_supplement' in report:
        raise BuildError('Could not find the official Stage1C snapshot')
    db = safe_path(ROOT, report['database_relative_path'], STAGE1C_DIR)
    if not db.is_file():
        raise BuildError('Official Stage1C database missing: ' + str(db))
    return db, report


def load_overture():
    manifest = read_json(ROOT / 'data' / 'manifests' / f'{SOURCE_KEY}.json')
    raw = safe_path(ROOT, manifest['relative_path'], Path('data') / 'raw')
    if manifest.get('bytes') != raw.stat().st_size:
        raise BuildError('Overture GeoJSON size differs from its manifest; export again')
    features = read_json(raw)['features']
    if len(features) != manifest['summary']['feature_count']:
        raise BuildError('Overture feature count differs from its manifest; export again')
    return manifest, features


def build_record(feature, index, manifest):
    p = feature['properties']
    rid = str(p['OBJECTID'])
    r = normalise_feature(feature, index, SOURCE, manifest, None, rid, 'source_property')
    r.pop('geometry', None)
    top, sub = p.get('OVERTURE_TOP_LEVEL'), p.get('OVERTURE_CATEGORY')
    category = app_category(top, sub)
    r['category'] = category
    r['category_basis'] = 'overture_taxonomy_mapped_to_project_category'
    r['source_status_raw'] = p.get('OPERATING_STATUS')
    r['provenance']['source_period_label'] = manifest.get('source_period_label')
    tags = [CATEGORY_LABEL[category], category]
    tags += TOP_LEVEL_ZH.get(top, '').split()
    tags += SUB_ZH.get(sub, '').split()
    if sub:
        tags.append(sub.replace('_', ' '))
    tags = list(dict.fromkeys(t for t in tags if t))
    r['overture'] = {
        'top_level': top, 'category': sub, 'hierarchy': p.get('OVERTURE_HIERARCHY'),
        'basic_category': p.get('BASIC_CATEGORY'), 'confidence': p.get('CONFIDENCE'),
        'operating_status': p.get('OPERATING_STATUS'), 'brand': p.get('BRAND'), 'phone': p.get('PHONE'),
        'contributing_datasets': p.get('CONTRIBUTING_DATASETS'), 'release': manifest.get('source_period_label'),
    }
    r['stage1b'] = {
        'record_uid': r['record_uid'], 'display_title': r['display_title'], 'display_description': None,
        'opening_hours_display': None, 'display_repairs': {}, 'name_aliases': [], 'search_tags': tags,
        'tag_basis': 'overture_category_dictionary_v1', 'review_hold': False,
        'default_exploration_visible': bool(r['map_eligible']),
        'warnings': ['entrance_unverified', 'current_opening_hours_unverified', 'dwell_time_unknown',
                     'third_party_merged_data_not_official'],
        'coordinate_role': 'source_point', 'source_period_label': manifest.get('source_period_label'),
        'planning_ready': False,
    }
    return r


def stored_record(r):
    """The source record as stored: only what the detail view and later review need."""
    keep = ('record_uid', 'source_key', 'source_record_id', 'name', 'address', 'external_url',
            'longitude', 'latitude', 'overture')
    out = {k: r.get(k) for k in keep}
    prov = r['provenance']
    out['provenance'] = {k: prov.get(k) for k in ('catalogue_url', 'licence_url', 'source_period_label', 'retrieved_at')}
    return out


def build_entity(r):
    tags = r['stage1b']['search_tags']
    o = r['overture']
    search = normal_name(' '.join([r['display_title'], *tags, str(r.get('address') or ''),
                                   str(o.get('brand') or '')]))
    lon, lat = r['longitude'], r['latitude']
    drawable = bool(r['map_eligible'] and lon is not None and lat is not None)
    return {
        'entity_id': 'place:' + r['record_uid'], 'display_title': r['display_title'],
        'primary_uid': r['record_uid'], 'member_uids': [r['record_uid']], 'member_count': 1,
        'identity_decision_id': None,
        'aliases': [r['display_title']], 'categories': [r['category']], 'category': r['category'],
        'search_tags': tags, 'search_text': search, 'address': r.get('address'),
        'longitude': lon, 'latitude': lat, 'map_eligible': drawable,
        'coordinate_kind': 'source_point', 'coordinate_source_uid': r['record_uid'],
        'coordinate_is_entrance': False,
        'source_status_holds': [], 'legacy_reviews': [], 'regular_hours_evidence': [],
        'default_exploration_visible': drawable, 'visit_status': 'not_current_open_verified',
        'related_records': [], 'park_boundary_candidates': [], 'access_point_candidates': [],
        'suggested_duration': None, 'planning_ready': False,
        'data_source': 'overture',
        'overture': {k: o.get(k) for k in ('top_level', 'category', 'confidence', 'brand')},
    }


def find_duplicates(records, official):
    """Overture record -> best official match within the radius, or None."""
    grid = defaultdict(list)
    for e in official:
        if isinstance(e.get('latitude'), (int, float)) and isinstance(e.get('longitude'), (int, float)):
            names = {normal_name(x) for x in [e['display_title'], *e.get('aliases', [])]} - {''}
            grid[cell(e['latitude'], e['longitude'])].append((e, names))
    result = {}
    for r in records:
        if r['latitude'] is None or not r['normalised_name']:
            continue
        ci, cj = cell(r['latitude'], r['longitude'])
        best = None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for e, names in grid.get((ci + di, cj + dj), []):
                    d = metres(r['latitude'], r['longitude'], e['latitude'], e['longitude'])
                    if d > DUPLICATE_RADIUS_M:
                        continue
                    s = max(similar(r['normalised_name'], n) for n in names)
                    if s >= NAME_SIMILARITY and (best is None or (s, -d) > (best[1], -best[2])):
                        best = (e, s, d)
        if best:
            result[r['record_uid']] = best
    return result


OVERTURE_DUPLICATE_RADIUS_M = 50.0


def hide_overture_internal_duplicates(db):
    """Same name within 50 m = one place recorded twice. Keep the most confident record, hide the rest.

    Chains (KFC, Starbucks) usually have branches further apart than this, so they are kept.
    """
    rows = [json.loads(r[0]) for r in db.execute(
        "SELECT data_json FROM entities WHERE entity_id LIKE 'place:overture_places:%' AND visible=1").fetchall()]
    groups = defaultdict(list)
    for e in rows:
        name = normal_name(e['display_title'])
        if name and e.get('latitude') is not None:
            groups[name].append(e)

    def rank(e):
        o = e.get('overture') or {}
        return (o.get('confidence') or 0, bool(e.get('address')), e['entity_id'])

    hidden = []
    for same in groups.values():
        if len(same) < 2:
            continue
        same.sort(key=rank, reverse=True)
        kept = []
        for e in same:
            twin = next((k for k in kept if metres(e['latitude'], e['longitude'], k['latitude'], k['longitude'])
                         <= OVERTURE_DUPLICATE_RADIUS_M), None)
            if twin is None:
                kept.append(e)
                continue
            e['default_exploration_visible'] = False
            e['visit_status'] = 'duplicate_record_hold'
            e['hidden_reason'] = f"Overture 重复记录：与 {twin['entity_id']} 同名且相距 50 米以内"
            e['duplicate_of'] = twin['entity_id']
            db.execute('UPDATE entities SET visible=0, data_json=? WHERE entity_id=?', (j(e), e['entity_id']))
            hidden.append(e['entity_id'])
    return hidden


def clean_official_titles(db):
    """Readable names for STB / NParks records; the old name stays as an alias for search."""
    changed = 0
    for (row,) in db.execute("SELECT data_json FROM entities WHERE entity_id NOT LIKE 'place:overture_places:%'").fetchall():
        e = json.loads(row)
        source = str(e.get('primary_uid', '')).split(':')[0]
        new = clean_title(source, e['display_title'])
        if new == e['display_title']:
            continue
        e['aliases'] = list(dict.fromkeys([*e.get('aliases', []), e['display_title']]))
        e['source_title'] = e['display_title']
        e['display_title'] = new
        e['search_text'] = normal_name(' '.join([e['search_text'], new]))
        db.execute('UPDATE entities SET title=?, search_text=?, data_json=? WHERE entity_id=?',
                   (new, e['search_text'], j(e), e['entity_id']))
        changed += 1
    return changed


def apply_duration_defaults(db):
    """Default short / regular / long minutes by detailed category (config/duration_defaults.csv)."""
    path = ROOT / 'config' / 'duration_defaults.csv'
    if not path.is_file():
        return {'applied': 0, 'note': 'config/duration_defaults.csv not found; skipped'}
    rules = load_rules(path)
    counts, unmatched = Counter(), 0
    for (row,) in db.execute('SELECT data_json FROM entities').fetchall():
        e = json.loads(row)
        rule = match_duration(rules, e)
        if rule is None:
            unmatched += 1
            continue
        e['suggested_duration'] = {'short': rule['short'], 'regular': rule['regular'], 'long': rule['long'],
                                   'basis': 'category_default', 'rule': rule['rule']}
        if rule.get('takeaway'):
            e['suggested_duration']['takeaway'] = rule['takeaway']
        db.execute('UPDATE entities SET data_json=? WHERE entity_id=?', (j(e), e['entity_id']))
        counts[rule['rule']] += 1
    return {'applied': sum(counts.values()), 'unmatched': unmatched, 'by_rule': dict(counts)}


def apply_core_review(db):
    """Write reviewed hours, durations and entrances (config/core_attractions.json) into entities."""
    path = ROOT / 'config' / 'core_attractions.json'
    if not path.is_file():
        return {'applied': 0, 'note': 'config/core_attractions.json not found; skipped'}
    review = json.loads(path.read_text(encoding='utf-8'))
    applied, missing, hidden, fixed = 0, [], [], []

    def load(eid):
        row = db.execute('SELECT data_json FROM entities WHERE entity_id=?', (eid,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(e):
        db.execute('UPDATE entities SET title=?, search_text=?, visible=?, data_json=? WHERE entity_id=?',
                   (e['display_title'], e['search_text'], int(e['default_exploration_visible']), j(e), e['entity_id']))

    for eid, p in review.get('places', {}).items():
        e = load(eid)
        if e is None:
            missing.append(eid)
            continue
        default = e.get('suggested_duration') or {}
        e['suggested_duration'] = p.get('suggested_duration')
        if e['suggested_duration'] and default.get('takeaway'):
            # keep the category takeaway time (e.g. Lau Pa Sat as a food centre) under the reviewed durations
            e['suggested_duration'] = {**e['suggested_duration'], 'takeaway': default['takeaway']}
        e['opening_hours_display'] = p.get('opening_hours')
        e['last_admission'] = p.get('last_admission')
        e['closed_note'] = p.get('closed')
        e['price_note'] = p.get('price')
        e['entrance_points'] = p.get('entrances', [])
        e['core_review'] = {k: p.get(k) for k in ('priority', 'status', 'checked_on', 'checked_by', 'source', 'note')}
        if p.get('display_title') and p['display_title'] != e['display_title']:
            e['aliases'] = list(dict.fromkeys([*e.get('aliases', []), e['display_title']]))
            e['display_title'] = p['display_title']
            e['search_text'] = normal_name(' '.join([e['search_text'], p['display_title']]))
        corr = p.get('correction') or {}
        if corr.get('coordinate_from_entrance'):
            ent = next((x for x in e['entrance_points'] if x['name'] == corr['coordinate_from_entrance']), None)
            if ent:
                e['source_coordinate'] = {'longitude': e['longitude'], 'latitude': e['latitude']}
                e['longitude'], e['latitude'] = ent['longitude'], ent['latitude']
                e['coordinate_kind'] = 'reviewed_correction'
                e['coordinate_note'] = corr.get('reason')
                fixed.append(eid)
        save(e)
        applied += 1
    # Overture copies of core attractions (e.g. a renamed official record) are hidden, not deleted.
    core = {eid: load(eid) for eid in review.get('places', {})}
    core_names = {eid: {normal_name(x) for x in [e['display_title'], *e.get('aliases', [])]} - {''}
                  for eid, e in core.items() if e}
    dup_hidden = []
    for (row,) in db.execute("SELECT data_json FROM entities WHERE entity_id LIKE 'place:overture_places:%'").fetchall():
        o = json.loads(row)
        if o['entity_id'] in core or not o.get('default_exploration_visible'):
            continue
        name = normal_name(o['display_title'])
        for eid, e in core.items():
            if not e:
                continue
            d = metres(o['latitude'], o['longitude'], e['latitude'], e['longitude'])
            # Same name within 1 km, or one name containing the other within 300 m.
            # Fuzzy similarity alone is not enough here ("Raffles City" is not "Raffles Hotel").
            if (name in core_names[eid] and d <= 1000) or (d <= 300 and any(similar(name, n) >= 0.9 for n in core_names[eid])):
                o['default_exploration_visible'] = False
                o['visit_status'] = 'duplicate_of_core_attraction'
                o['hidden_reason'] = f"与核心景点重复：{e['display_title']}"
                save(o)
                dup_hidden.append(o['entity_id'])
                break
    for eid, reason in review.get('hide', {}).items():
        e = load(eid)
        if e is None:
            continue
        e['default_exploration_visible'] = False
        e['visit_status'] = 'replaced_record_hold'
        e['hidden_reason'] = reason
        save(e)
        hidden.append(eid)
    return {'applied': applied, 'missing_entities': missing, 'hidden': hidden, 'coordinate_corrections': fixed,
            'overture_duplicates_of_core_hidden': dup_hidden,
            'exported_on': review.get('exported_on')}


def apply_osm_hours(db):
    """Reference opening hours from OSM for places that have none (data/raw/osm/osm_opening_hours.json)."""
    raw = osm_opening_hours.RAW
    if not raw.is_file():
        return {'applied': 0, 'note': 'No OSM download; run scripts/osm_opening_hours.py fetch'}
    osm = json.loads(raw.read_text(encoding='utf-8'))
    ents = [json.loads(r[0]) for r in db.execute('SELECT data_json FROM entities WHERE visible=1 AND map_eligible=1').fetchall()]
    ents = [e for e in ents if isinstance(e.get('latitude'), (int, float)) and isinstance(e.get('longitude'), (int, float))]
    matches = osm_opening_hours.match_all(osm['places'], ents)
    by_id = {e['entity_id']: e for e in ents}
    for eid, m in matches.items():
        e = by_id[eid]
        e['opening_hours_display'] = f"{m['opening_hours']}（OSM 参考）"
        e['opening_hours_source'] = 'osm_reference'
        e['opening_hours_osm'] = {k: m[k] for k in ('osm_id', 'osm_name', 'opening_hours', 'distance_m', 'name_score')}
        db.execute('UPDATE entities SET data_json=? WHERE entity_id=?', (j(e), eid))
    return {'applied': len(matches), 'osm_places_with_hours': osm.get('count'), 'osm_retrieved_at': osm.get('retrieved_at'),
            'licence': 'ODbL; © OpenStreetMap contributors'}


def rollback():
    ptr = read_json(POINTER)
    report = read_json(safe_path(ROOT, ptr['report_relative_path'], STAGE1C_DIR))
    base = report.get('overture_supplement', {}).get('base_build_id')
    if not base:
        print('The current catalogue has no Overture data. Nothing to undo.')
        return
    base_dir = f'{STAGE1C_DIR.as_posix()}/{base}'
    dump_json(POINTER, {'build_id': base, 'database_relative_path': f'{base_dir}/entities.sqlite3',
                        'report_relative_path': f'{base_dir}/stage1c_profile.json'})
    print(f'Pointer moved back to the official snapshot {base}. Overture snapshot folders were kept.')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rollback', action='store_true', help='point the app back to the official snapshot')
    ap.add_argument('--no-publish', action='store_true', help='build the snapshot but do not move the pointer')
    args = ap.parse_args()
    if args.rollback:
        return rollback()

    base_db, base_report = current_official_snapshot()
    print(f'[READ] official snapshot {base_report["build_id"]} (read-only)')
    manifest, features = load_overture()
    print(f'[READ] Overture: {len(features)} places, {manifest.get("source_period_label")}')

    with sqlite3.connect(base_db.resolve().as_uri() + '?mode=ro', uri=True) as src:
        official = [json.loads(x[0]) for x in src.execute('SELECT data_json FROM entities')]
        existing = src.execute('SELECT COUNT(*) FROM source_records WHERE source_key=?', (SOURCE_KEY,)).fetchone()[0]
    if existing:
        raise BuildError('Base snapshot already contains Overture records')

    records, skipped_invalid = [], 0
    excluded = Counter()
    seen = set()
    for i, f in enumerate(features):
        sub = f['properties'].get('OVERTURE_CATEGORY')
        if sub in EXCLUDED_SUBCATEGORIES:
            excluded[sub] += 1
            continue
        r = build_record(f, i, manifest)
        if r['record_uid'] in seen:
            raise BuildError('Repeated Overture id: ' + r['record_uid'])
        seen.add(r['record_uid'])
        if not r['map_eligible']:
            skipped_invalid += 1
        records.append(r)

    print(f'[FILTER] {sum(excluded.values())} excluded by sub-category: {dict(excluded)}')
    dups = find_duplicates(records, official)
    kept = [r for r in records if r['record_uid'] not in dups]
    entities = [build_entity(r) for r in kept]
    print(f'[MATCH] {len(dups)} probably same as an official place -> not added (see review list)')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out = ROOT / STAGE1C_DIR / stamp
    out.mkdir(parents=True, exist_ok=False)
    dbpath = out / 'entities.sqlite3'
    try:
        with sqlite3.connect(base_db.resolve().as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(dbpath) as dst:
            src.backup(dst)
        db = sqlite3.connect(dbpath)
        db.execute('PRAGMA foreign_keys=ON')
        for r in kept:
            db.execute('INSERT INTO source_records VALUES(?,?,?)', (r['record_uid'], SOURCE_KEY, j(stored_record(r))))
        for e in entities:
            db.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?)', (
                e['entity_id'], e['primary_uid'], e['display_title'], e['category'], e['search_text'],
                int(e['default_exploration_visible']), int(e['map_eligible']), j(e)))
            db.execute('INSERT INTO members VALUES(?,?)', (e['primary_uid'], e['entity_id']))

        renamed = clean_official_titles(db)
        internal_dups = hide_overture_internal_duplicates(db)
        durations = apply_duration_defaults(db)
        core = apply_core_review(db)
        osm_hours = apply_osm_hours(db)
        total_entities = db.execute('SELECT COUNT(*) FROM entities').fetchone()[0]
        visible = db.execute('SELECT COUNT(*) FROM entities WHERE visible=1').fetchone()[0]
        supplement = {
            'base_build_id': base_report['build_id'], 'source_key': SOURCE_KEY,
            'release': manifest.get('source_period_label'), 'export_filters': manifest.get('filters'),
            'exported_records': len(features), 'excluded_by_subcategory': dict(excluded),
            'added_entities': len(entities),
            'skipped_probable_official_duplicates': len(dups), 'records_without_drawable_point': skipped_invalid,
            'added_by_app_category': dict(Counter(e['category'] for e in entities)),
            'duplicate_rule': f'within {DUPLICATE_RADIUS_M:.0f} m of an official entity and name similarity >= {NAME_SIMILARITY}',
            'category_mapping': 'overture_taxonomy_to_existing_5_categories_v1',
            'licence_url': SOURCE['licence_url'],
            'cautions': ['Overture is third-party merged data, not an official source.',
                         'No opening hours; operating_status only says the place is not marked closed.',
                         'Duplicates among Overture records and with official records may remain.'],
        }
        report = dict(base_report)
        report.update({
            'build_id': stamp, 'generated_at': datetime.now(timezone.utc).isoformat(),
            'source_record_counts': {**base_report['source_record_counts'], SOURCE_KEY: len(kept)},
            'input_candidate_source_records': base_report['input_candidate_source_records'] + len(kept),
            'entity_display_count': total_entities, 'default_exploration_entity_count': visible,
            'snapshot_relative_path': (STAGE1C_DIR / stamp).as_posix(),
            'database_relative_path': (STAGE1C_DIR / stamp / 'entities.sqlite3').as_posix(),
            'overture_supplement': supplement,
            'core_review': core,
            'official_titles_cleaned': renamed,
            'duration_defaults': durations,
            'osm_opening_hours': osm_hours,
            'attribution': ATTRIBUTION,
            'overture_internal_duplicates_hidden': len(internal_dups),
        })
        db.execute("UPDATE metadata SET value_json=? WHERE key='report'", (j(report),))
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise BuildError('Foreign-key check failed')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise BuildError('SQLite quick_check failed')
        db.commit()
        db.execute(f'PRAGMA page_size={PAGE_SIZE}')
        db.execute('VACUUM')
        db.close()

        base_dir = base_db.parent
        for name in ('identity_decisions.json', 'access_semantic_reviews.json', 'evidence_config.json', 'review_findings.md'):
            if (base_dir / name).is_file():
                shutil.copy2(base_dir / name, out / name)
        dump_json(out / 'stage1c_profile.json', report)
        dump_json(out / 'overture_possible_duplicates.json', {
            'rule': supplement['duplicate_rule'], 'count': len(dups),
            'pairs': [{'overture_uid': uid, 'overture_name': next(r['name'] for r in records if r['record_uid'] == uid),
                       'official_entity_id': e['entity_id'], 'official_name': e['display_title'],
                       'name_similarity': round(s, 3), 'straight_line_metres': round(d, 1), 'decision': 'not_added_needs_review'}
                      for uid, (e, s, d) in dups.items()]})
    except Exception as exc:
        dump_json(out / 'FAILED.json', {'error_type': type(exc).__name__, 'error': str(exc),
                                        'previous_snapshots_unchanged': True})
        raise

    if not args.no_publish:
        dump_json(POINTER, {'build_id': stamp, 'database_relative_path': report['database_relative_path'],
                            'report_relative_path': (STAGE1C_DIR / stamp / 'stage1c_profile.json').as_posix()})
    print(f'[ADD] {len(entities)} Overture places added: {supplement["added_by_app_category"]}')
    print(f'[NAMES] {renamed} official names cleaned (old names kept as aliases)')
    print(f'[DEDUP] {len(internal_dups)} Overture records hidden as duplicates of another Overture record (same name, <= 50 m)')
    print(f"[DURATION] category defaults set for {durations.get('applied', 0)} places"
          f"{'; unmatched ' + str(durations['unmatched']) if durations.get('unmatched') else ''} (core attractions override below)")
    print(f"[REVIEW] core attractions applied: {core.get('applied', 0)}; hidden: {len(core.get('hidden', []))}; "
          f"coordinate fixes: {len(core.get('coordinate_corrections', []))}; "
          f"Overture copies of core places hidden: {len(core.get('overture_duplicates_of_core_hidden', []))}"
          + (f"; NOT FOUND: {core['missing_entities']}" if core.get('missing_entities') else ''))
    print(f"[HOURS] OSM reference opening hours added to {osm_hours.get('applied', 0)} places"
          + (f" ({osm_hours['note']})" if osm_hours.get('note') else ''))
    print(f'[TOTAL] {total_entities} entities, {visible} shown by default')
    print(f'[DONE] {out}' + ('' if args.no_publish else '  (app now uses this snapshot)'))
    print('Undo: python scripts/add_overture_to_catalogue.py --rollback')


if __name__ == '__main__':
    main()
