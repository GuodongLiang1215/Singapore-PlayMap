"""Deterministic review of all candidate records. No web requests or routing.

Source records remain unchanged. Only explicitly reviewed pairs are grouped.
Record IDs alone never authorize a correction: name/address/coordinate guards
must still agree with the source snapshot that was reviewed.
"""
from __future__ import annotations

import copy
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from app.stage1.normalise import normal_name

GUARD_FIELDS = ('record_uid', 'source_key', 'name', 'address', 'longitude', 'latitude')
ACTIONS = {'same_place', 'keep_separate', 'related_not_same'}
SINGAPORE_TZ = timezone(timedelta(hours=8))


class ReviewError(ValueError):
    """Input or review schema is inconsistent; do not publish a partial build."""


def local_date() -> date:
    return datetime.now(SINGAPORE_TZ).date()


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ReviewError('Invalid review date') from exc


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _urls(values):
    if not isinstance(values, list):
        raise ReviewError('web_sources must be a list')
    for url in values:
        p = urlsplit(url)
        if p.scheme not in ('https', 'http') or not p.netloc or p.username or p.password:
            raise ReviewError('Invalid public evidence URL')


def _guard_valid_shape(guard):
    if not isinstance(guard, dict) or not all(k in guard for k in GUARD_FIELDS):
        raise ReviewError('Every review must have complete record guards')
    for k in ('longitude', 'latitude'):
        if guard[k] is not None and not _number(guard[k]):
            raise ReviewError('Invalid guard coordinates')


def guard_matches(record: dict | None, expected: dict) -> bool:
    if record is None:
        return False
    for k in GUARD_FIELDS:
        a, b = record.get(k), expected[k]
        if k in ('longitude', 'latitude') and a is not None and b is not None:
            if not (_number(a) and _number(b)) or abs(a - b) > 1e-8:
                return False
        elif a != b:
            return False
    return True


def validate_config(config: dict) -> None:
    if config.get('schema_version') != 1:
        raise ReviewError('Unsupported review configuration')
    _date(config.get('reviewed_on'))
    seen_ids, seen_pairs = set(), set()
    for d in config.get('identity_decisions', []):
        if d.get('action') not in ACTIONS:
            raise ReviewError('Unknown identity action')
        members = d.get('member_uids', [])
        guards = d.get('expected_records', [])
        if len(members) != 2 or len(set(members)) != 2 or len(guards) != 2:
            raise ReviewError('Identity review must describe exactly two distinct records')
        for g in guards:
            _guard_valid_shape(g)
        if {g['record_uid'] for g in guards} != set(members):
            raise ReviewError('Guards do not match the reviewed pair')
        pair = tuple(sorted(members))
        if d.get('decision_id') in seen_ids or pair in seen_pairs:
            raise ReviewError('Duplicate identity review')
        seen_ids.add(d['decision_id']); seen_pairs.add(pair)
        if d['action'] == 'same_place' and d.get('primary_uid') not in members:
            raise ReviewError('Reviewed same-place group needs a member as its display primary')
        _date(d.get('reviewed_on')); _urls(d.get('web_sources', []))
    for kind in ('legacy_reviews', 'hours_reviews'):
        seen_records = set()
        for d in config.get(kind, []):
            _guard_valid_shape(d.get('expected_record'))
            if d.get('record_uid') != d['expected_record']['record_uid']:
                raise ReviewError('Correction guard ID mismatch')
            if d['record_uid'] in seen_records:
                raise ReviewError('Duplicate field review for a source record')
            seen_records.add(d['record_uid'])
            _date(d.get('checked_on')); _urls(d.get('web_sources', []))
            if not d.get('web_sources'):
                raise ReviewError('Online field reviews require public evidence')
            if kind == 'legacy_reviews':
                if d.get('kind') not in ('closed_at_old_location', 'relocated_old_coordinates'):
                    raise ReviewError('Unsupported legacy correction')
                if d.get('replacement', {}).get('longitude') is not None or d.get('replacement', {}).get('latitude') is not None:
                    raise ReviewError('This review cannot create replacement coordinates')
            else:
                w = d.get('weekly_minutes', {})
                if set(w) != {str(i) for i in range(7)}:
                    raise ReviewError('Regular-hours evidence needs seven explicitly coded days')
                for intervals in w.values():
                    if not isinstance(intervals, list):
                        raise ReviewError('Invalid regular-hours intervals')
                    last = -1
                    for pair in intervals:
                        if len(pair) != 2 or any(type(x) is not int for x in pair):
                            raise ReviewError('Hours must be integer minute offsets')
                        lo, hi = pair
                        if not 0 <= lo < hi <= 1440 or lo < last:
                            raise ReviewError('Hours are out of range or overlap')
                        last = hi
                days = d.get('recheck_after_days')
                if type(days) is not int or not 1 <= days <= 365:
                    raise ReviewError('Invalid recheck policy')
                if d.get('timezone') != 'Asia/Singapore':
                    raise ReviewError('Regular schedules use Singapore local time')


def _normal_target(value: str | None) -> str:
    n = normal_name(value or '')
    n = re.sub(r'\bpk\b', 'park', n)
    n = re.sub(r'\bccnr\b', 'central catchment nature reserve', n)
    n = n.replace('spring leaf', 'springleaf')
    return ' '.join(n.split())


def review_access_link(link: dict) -> dict:
    """Only compare explicit source labels; never infer legal/public access."""
    out = copy.deepcopy(link)
    name = str(link.get('access_name') or '')
    boundary = _normal_target(link.get('boundary_name'))
    match = re.match(r'^\s*entry\s+points?\s+to\s+(.+?)\s*$', name, flags=re.I)
    target = None
    if not match:
        status = 'unspecified_or_unresolved'
        explanation = '源名称未明确写出目标园区；空间靠近只能作为待核验线索。'
    else:
        raw_target = match.group(1)
        target = _normal_target(raw_target.split(',')[0])
        contextual_ccnr = bool(re.search(r'\bccnr\b', raw_target, re.I))
        if target == boundary:
            status = 'name_supports_target'
            explanation = '源名称与该边界名称相符；仅加强候选关系，不确认入口开放或可达。'
        elif target in ('labrador', 'macritchie'):
            status = 'broad_target_label'
            explanation = '源名称使用较宽泛地名，无法唯一确认具体公园/保护区；保留候选但不自动选择。'
        elif contextual_ccnr and boundary == 'central catchment nature reserve':
            status = 'broader_area_context'
            explanation = '名称提到CCNR范围，但未证明这是该保护区任意位置的通用入口。'
        else:
            status = 'explicit_target_differs'
            explanation = '源名称指向的地方与邻近边界名称不同；不能因相邻就自动当作此园区入口。可能是连接点，需核验。'
    out['semantic_review'] = {
        'status': status, 'explicit_target_normalised': target,
        'boundary_name_normalised': boundary, 'explanation_zh': explanation,
        'method': 'conservative_source_label_comparison_v1_not_topology',
        'entrance_verified': False, 'auto_use_as_entrance': False,
        'source_link_retained': True,
    }
    return out


def _hours_view(item: dict, as_of: date) -> dict:
    d = copy.deepcopy(item)
    checked = _date(d['checked_on'])
    d['recheck_on'] = (checked + timedelta(days=d['recheck_after_days'])).isoformat()
    d['freshness'] = 'within_review_period' if checked <= as_of < _date(d['recheck_on']) else 'needs_recheck'
    d['as_of'] = as_of.isoformat()
    d['is_open_now'] = None
    d['live_open_verified'] = False
    d['special_dates_verified'] = False
    return d


def process(records: list[dict], park_links: list[dict], access_links: list[dict],
            config: dict, as_of: date | None = None) -> dict:
    validate_config(config)
    as_of = as_of or local_date()
    by_uid = {}
    for r in records:
        uid = r.get('record_uid')
        if not isinstance(uid, str) or not uid or uid in by_uid:
            raise ReviewError('Missing or repeated source record UID')
        if r.get('role') != 'candidate_places':
            raise ReviewError('Input catalogue contains a non-destination source record')
        by_uid[uid] = r
    if not records:
        raise ReviewError('An empty candidate catalogue is not a nationwide success')

    applied, unapplied = [], []
    groups, member_group = {}, {}
    for original in config.get('identity_decisions', []):
        d = copy.deepcopy(original)
        ok = (_date(d['reviewed_on']) <= as_of and
              all(guard_matches(by_uid.get(g['record_uid']), g) for g in d['expected_records']))
        d['applied'] = ok
        if not ok:
            d['unapplied_reason'] = 'record_changed_missing_or_review_date_in_future'
            unapplied.append(d); continue
        if d['action'] == 'same_place':
            if any(uid in member_group for uid in d['member_uids']):
                raise ReviewError('Overlapping same-place groups require an explicit reviewed group; no transitive auto-merge')
            gid = 'place:' + d['primary_uid']
            groups[gid] = {'members': d['member_uids'], 'primary': d['primary_uid'], 'decision': d}
            for uid in d['member_uids']:
                member_group[uid] = gid
        applied.append(d)
    for uid in by_uid:
        if uid not in member_group:
            gid = 'place:' + uid
            member_group[uid] = gid
            groups[gid] = {'members': [uid], 'primary': uid, 'decision': None}
    for d in applied:
        if d['action'] != 'same_place' and len({member_group[u] for u in d['member_uids']}) != 2:
            raise ReviewError('A keep-separate/related decision conflicts with an accepted identity group')

    legacy_by_uid, hours_by_uid = {}, {}
    field_applied, field_unapplied = [], []
    for kind, dest in [('legacy_reviews', legacy_by_uid), ('hours_reviews', hours_by_uid)]:
        for original in config.get(kind, []):
            d = copy.deepcopy(original)
            if not guard_matches(by_uid.get(d['record_uid']), d['expected_record']) or _date(d['checked_on']) > as_of:
                field_unapplied.append({'review_id': d['review_id'], 'record_uid': d['record_uid'],
                                       'reason': 'record_changed_missing_or_review_date_in_future'})
                continue
            dest[d['record_uid']] = _hours_view(d, as_of) if kind == 'hours_reviews' else d
            field_applied.append({'review_id': d['review_id'], 'record_uid': d['record_uid'], 'kind': kind})

    reviewed_access = [review_access_link(x) for x in access_links]
    link_keys = [(a['access_uid'], a['boundary_uid']) for a in reviewed_access]
    if len(link_keys) != len(set(link_keys)):
        raise ReviewError('Duplicate access/boundary links')
    park_index, access_index = defaultdict(list), defaultdict(list)
    for x in park_links:
        park_index[x['park_uid']].append(x)
    for x in reviewed_access:
        access_index[x['boundary_uid']].append(x)
    related = defaultdict(list)
    for d in applied:
        if d['action'] == 'same_place':
            continue
        left, right = (member_group[uid] for uid in d['member_uids'])
        for here, other in ((left, right), (right, left)):
            related[here].append({'entity_id': other, 'action': d['action'], 'reason_zh': d['reason_zh'],
                                  'decision_id': d['decision_id']})

    entities = []
    for gid, group in sorted(groups.items()):
        primary = by_uid[group['primary']]
        members = [by_uid[uid] for uid in group['members']]
        aliases = sorted({str(v) for r in members for v in
            (r.get('name'), r.get('display_title'), r.get('stage1b', {}).get('display_title')) if v})
        e = primary.get('stage1b', {})
        title = (group['decision'] or {}).get('display_name') or e.get('display_title') or primary.get('display_title') or primary['name']
        lon, lat = primary.get('longitude'), primary.get('latitude')
        drawable = bool(primary.get('map_eligible') and _number(lon) and _number(lat) and -180 <= lon <= 180 and -90 <= lat <= 90)
        source_holds = [r['record_uid'] for r in members if r.get('stage1b', {}).get('review_hold')]
        legacy = [legacy_by_uid[r['record_uid']] for r in members if r['record_uid'] in legacy_by_uid]
        hours = [hours_by_uid[r['record_uid']] for r in members if r['record_uid'] in hours_by_uid]
        boundaries, links = {}, {}
        for r in members:
            for b in park_index[r['record_uid']]:
                boundaries[(b['park_uid'], b['boundary_uid'])] = b
                if b.get('access_propagation_candidate'):
                    for a in access_index[b['boundary_uid']]:
                        links[(a['access_uid'], a['boundary_uid'])] = a
        categories = sorted({r['category'] for r in members})
        tags = sorted({v for r in members for v in r.get('stage1b', {}).get('search_tags', [])})
        replacements = [l['replacement'] for l in legacy]
        search = normal_name(' '.join([title, *aliases, *tags,
            *[str(r.get('address') or '') for r in members],
            *[str(r.get('stage1b', {}).get('display_description') or r.get('description') or '') for r in members],
            *[str(l['name']) for l in replacements]]))
        view = {
            'entity_id': gid, 'display_title': title, 'primary_uid': group['primary'],
            'member_uids': list(group['members']), 'member_count': len(members),
            'identity_decision_id': (group['decision'] or {}).get('decision_id'),
            'identity_level': 'reviewed_source_identity_group' if group['decision'] else 'single_source_unresolved_identity',
            'identity_is_not_access_verification': True,
            'aliases': aliases, 'categories': categories, 'category': primary['category'], 'search_tags': tags,
            'search_text': search, 'address': primary.get('address'),
            'longitude': lon, 'latitude': lat, 'map_eligible': drawable,
            'coordinate_kind': primary.get('coordinate_kind'), 'coordinate_source_uid': group['primary'],
            'coordinate_is_entrance': False, 'coordinates_averaged': False,
            'coordinate_options': [{k: r.get(k) for k in ('record_uid', 'longitude', 'latitude', 'coordinate_kind', 'address')} for r in members],
            'source_status_holds': source_holds, 'legacy_reviews': legacy, 'regular_hours_evidence': hours,
            'default_exploration_visible': bool(drawable and not source_holds and not legacy),
            'visit_status': 'legacy_record_hold' if legacy else ('source_status_review_hold' if source_holds else 'not_current_open_verified'),
            'related_records': related[gid], 'park_boundary_candidates': list(boundaries.values()),
            'access_point_candidates': list(links.values()),
            'suggested_duration': None, 'planning_ready': False,
            'capabilities': {'browse_candidate': drawable, 'regular_hours_evidence': bool(hours),
                             'verified_entrance_routing': False, 'live_open_validation': False,
                             'itinerary_planning': False},
        }
        entities.append(view)

    if sum(e['member_count'] for e in entities) != len(records):
        raise ReviewError('Source-to-entity grouping lost records')
    stats = {
        'input_candidate_source_records': len(records), 'entity_display_count': len(entities),
        'reviewed_group_count': len([g for g in groups.values() if g['decision']]),
        'display_record_reduction': len(records) - len(entities), 'deleted_source_records': 0,
        'identity_review_action_counts': dict(Counter(d['action'] for d in applied)),
        'identity_reviews_applied': len(applied), 'identity_reviews_unapplied': len(unapplied),
        'field_reviews_applied': len(field_applied), 'field_reviews_unapplied': len(field_unapplied),
        'legacy_hold_entities': sum(bool(e['legacy_reviews']) for e in entities),
        'historical_source_status_hold_entities': sum(bool(e['source_status_holds']) for e in entities),
        'default_exploration_entity_count': sum(e['default_exploration_visible'] for e in entities),
        'regular_hours_entity_count': sum(bool(e['regular_hours_evidence']) for e in entities),
        'regular_hours_entity_count_within_review_period': sum(any(h['freshness'] == 'within_review_period' for h in e['regular_hours_evidence']) for e in entities),
        'access_boundary_links_retained': len(reviewed_access),
        'access_source_points_with_links_in_input': len({a['access_uid'] for a in access_links}),
        'access_semantic_status_counts': dict(Counter(a['semantic_review']['status'] for a in reviewed_access)),
        'new_entrance_assignments': 0, 'verified_entrances': 0, 'new_coordinate_generation': False,
        'all_singapore_attractions_verified': False,
    }
    return {'entities': entities, 'member_to_entity': member_group, 'access_link_reviews': reviewed_access,
            'identity_reviews': applied + unapplied, 'field_reviews_applied': field_applied,
            'field_reviews_unapplied': field_unapplied, 'stats': stats,
            'as_of': as_of.isoformat(), 'original_records_modified': False}
