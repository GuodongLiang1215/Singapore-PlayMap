"""Never converts a source point into a verified entrance or deletes duplicates.

Every source feature is kept. Malformed/unknown-CRS geometries are retained but
not rendered. Geometry checks are structural, NOT a topology/access audit.
No network, no model, no geographic subset, and no inferred opening hours.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

CANDIDATE_SOURCES = {
    'stb_attractions': 'tourism', 'nparks_parks': 'nature',
    'nhb_historic_sites': 'heritage', 'nhb_monuments': 'monument',
    'nea_hawker_centres': 'food',
}
PROFILE_FIELDS = ('CLASS', 'STATUS', 'N_RESERVE', 'TYPE', 'PARK_TYPE',
                  'ALLOW_WALKING', 'ALLOW_CYCLING', 'ALLOW_PMD', 'ALLOW_WHEELING')
NULL_TEXT = {'', 'null', 'none', 'n/a', 'na', '<null>', '-', '--'}

class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if not self.hidden and tag in ('p', 'br', 'div', 'li', 'tr'): self.parts.append(' ')
    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden: self.hidden -= 1
        if not self.hidden: self.parts.append(' ')
    def handle_data(self, text):
        if not self.hidden: self.parts.append(text)

def text(value):
    if value is None: return None
    if isinstance(value, (dict, list)): value = json.dumps(value, ensure_ascii=False)
    value = str(value).strip()
    if value.casefold() in NULL_TEXT: return None
    if '<' in value and '>' in value:
        parser = TextOnly(); parser.feed(value); value = ''.join(parser.parts)
    value = re.sub(r'\s+', ' ', value).strip()
    return value or None

def normal_name(value):
    value = unicodedata.normalize('NFKC', text(value) or '').casefold()
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in value).split())

def safe_url(value):
    value = text(value)
    if not value: return None
    try:
        p = urlparse(value)
        if p.scheme.lower() in ('http', 'https') and p.hostname and not p.username and not p.password:
            return value
    except ValueError:
        pass
    return None

def first(props, *keys):
    return next((text(props.get(k)) for k in keys if text(props.get(k)) is not None), None)

def numeric(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

def geometry_check(g, crs=None):
    """Return drawable, lon/lat bbox, issues. Never reproject or swap silently."""
    problems = []
    if crs:
        name = str((crs.get('properties') or {}).get('name', '')) if isinstance(crs, dict) else ''
        if name not in {'EPSG:4326', 'urn:ogc:def:crs:EPSG::4326',
                        'urn:ogc:def:crs:OGC:1.3:CRS84', 'urn:ogc:def:crs:OGC::CRS84'}:
            return False, None, ['crs_requires_review']
    if not isinstance(g, dict): return False, None, ['missing_or_invalid_geometry']
    points = []
    def point(p):
        if not isinstance(p, list) or len(p) < 2 or not all(numeric(v) for v in p):
            raise ValueError('invalid_position')
        if not -180 <= p[0] <= 180 or not -90 <= p[1] <= 90:
            raise ValueError('coordinate_out_of_lonlat_range')
        points.append(p)
    def line(values, ring=False):
        if not isinstance(values, list) or len(values) < (4 if ring else 2):
            raise ValueError('invalid_ring' if ring else 'invalid_line')
        for p in values: point(p)
        if ring and values[0] != values[-1]: raise ValueError('unclosed_ring')
    def polygon(values):
        if not isinstance(values, list) or not values: raise ValueError('empty_polygon')
        for ring in values: line(ring, True)
    typ, c = g.get('type'), g.get('coordinates')
    try:
        if typ == 'Point': point(c)
        elif typ == 'LineString': line(c)
        elif typ == 'Polygon': polygon(c)
        elif typ in ('MultiPoint', 'MultiLineString', 'MultiPolygon'):
            if not isinstance(c, list) or not c: raise ValueError('empty_multi_geometry')
            fn = {'MultiPoint': point, 'MultiLineString': line, 'MultiPolygon': polygon}[typ]
            for part in c: fn(part)
        else: raise ValueError('unsupported_geometry_type')
    except (ValueError, TypeError, KeyError) as exc:
        problems.append(str(exc)); return False, None, problems
    xs = [p[0] for p in points]; ys = [p[1] for p in points]
    return True, [min(xs), min(ys), max(xs), max(ys)], problems

def record_id(feature, source_key, index):
    props = feature.get('properties') if isinstance(feature.get('properties'), dict) else {}
    fields = ('UNIQUEID', 'OBJECTID', 'OBJECTID_1') if source_key == 'nparks_facilities' else ('OBJECTID_1', 'OBJECTID', 'UNIQUEID')
    value = first(props, *fields)
    if value is not None: return value, 'source_property'
    if feature.get('id') is not None: return str(feature['id']), 'feature_id'
    return f'row-{index}', 'row_index_fallback'

def normalise_feature(feature, index, source, manifest, crs, rid, id_basis, repeated_id=False):
    key = source['key']; issues = []
    p = feature.get('properties')
    if not isinstance(p, dict): p = {}; issues.append('properties_missing_or_invalid')
    role = source['role']
    uid = key + ':' + quote(rid, safe='')
    if repeated_id: uid += f':row-{index}'; issues.append('repeated_source_record_id')
    if id_basis == 'row_index_fallback': issues.append('row_based_id_not_stable_across_snapshots')
    name = first(p, 'PAGETITLE') if key == 'stb_attractions' else first(p, 'NAME')
    # PARK names describe the containing park, not an independently named track.
    title = name or (f"步道片段 · {first(p, 'PARK')}" if key == 'nparks_tracks' and first(p, 'PARK') else f'未命名记录 · {rid}')
    if not name and role == 'candidate_places': issues.append('missing_name')
    ok, bounds, geometry_issues = geometry_check(feature.get('geometry'), crs)
    issues.extend(geometry_issues)
    typ = (feature.get('geometry') or {}).get('type') if isinstance(feature.get('geometry'), dict) else None
    if typ not in source['expected_geometry_types']:
        ok = False; issues.append('unexpected_source_geometry_type')
    point = feature.get('geometry', {}).get('coordinates') if ok and typ == 'Point' else None
    opening = first(p, 'OPENING_HOURS')
    address = first(p, 'ADDRESS', 'ADDRESS_MYENV')
    if not address:
        parts = [first(p, k) for k in ('ADDRESSBLOCKHOUSENUMBER', 'ADDRESSSTREETNAME', 'ADDRESSBUILDINGNAME', 'ADDRESSPOSTALCODE')]
        address = ' '.join(dict.fromkeys(v for v in parts if v)) or None
    description = first(p, 'OVERVIEW', 'META_DESCRIPTION', 'DESCRIPTION', 'ADDITIONAL_INFO')
    website = safe_url(first(p, 'EXTERNAL_LINK', 'HYPERLINK'))
    if role == 'candidate_places':
        if not opening: issues.append('opening_hours_missing')
        else: issues.append('opening_hours_unverified_source_text')
    coordinate_kind = 'representative_point' if key == 'nparks_parks' else ('source_point' if typ == 'Point' else 'source_geometry')
    return {
        'record_uid': uid, 'source_key': key, 'source_record_id': rid, 'source_row': index,
        'id_basis': id_basis, 'role': role, 'category': CANDIDATE_SOURCES.get(key, role),
        'category_basis': 'dataset_role_not_manual_interest_labelling',
        'name': name, 'display_title': title, 'normalised_name': normal_name(name),
        'address': address, 'description': description, 'external_url': website,
        'opening_hours_raw': opening,
        'opening_verification': 'unverified' if opening else 'unknown',
        'source_status_raw': p.get('STATUS'),
        'access_status': 'unknown', 'entrance_status': 'unverified', 'verified_entrances': [],
        'suggested_duration': None, 'price_status': 'unknown', 'planning_ready': False,
        'longitude': point[0] if point else None, 'latitude': point[1] if point else None,
        'coordinate_kind': coordinate_kind, 'geometry_type': typ,
        'map_eligible': ok, 'bounds': bounds, 'geometry': feature.get('geometry'),
        'geometry_check_level': 'structural_only_not_topology_or_access',
        'crs_interpretation': 'GeoJSON_lonlat_convention_not_survey_validation' if not crs else 'recognised_legacy_lonlat_metadata',
        'data_issues': issues,
        'provenance': {
            'dataset_id': source['dataset_id'], 'catalogue_url': source['catalogue_url'],
            'licence_url': source.get('licence_url'),
            'source_period_label': manifest.get('source_period_label'),
            'retrieved_at': manifest.get('retrieved_at'), 'raw_relative_path': manifest['relative_path'],
        },
    }

def metres(a, b):
    """Great-circle separation for duplicate REVIEW only. Never route distance."""
    lat1, lat2 = math.radians(a['latitude']), math.radians(b['latitude'])
    dlat = lat2-lat1; dlon = math.radians(b['longitude']-a['longitude'])
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, h))))

def duplicate_candidates(records):
    """Suggestions only; absolutely no merging and no canonical identity claim."""
    points = [r for r in records if r['role'] == 'candidate_places' and r['map_eligible'] and r['normalised_name'] and r['latitude'] is not None]
    result = []
    for i, a in enumerate(points):
        for b in points[i+1:]:
            exact = a['normalised_name'] == b['normalised_name']
            if not exact and (abs(a['latitude']-b['latitude']) > .002 or abs(a['longitude']-b['longitude']) > .002): continue
            distance = metres(a, b)
            similarity = 1.0 if exact else SequenceMatcher(None, a['normalised_name'], b['normalised_name']).ratio()
            if exact:
                reason = 'same_normalised_name_nearby' if distance <= 150 else 'same_name_distant_check_identity'
            elif distance <= 100 and similarity >= .82:
                reason = 'nearby_similar_name'
            else: continue
            result.append({'left_uid': a['record_uid'], 'right_uid': b['record_uid'],
                'left_name': a['name'], 'right_name': b['name'],
                'left_source': a['source_key'], 'right_source': b['source_key'],
                'straight_line_metres': round(distance, 2), 'name_similarity': round(similarity, 4),
                'reason': reason, 'auto_merged': False, 'decision': 'needs_review'})
    return result
