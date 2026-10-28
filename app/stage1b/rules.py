"""Deterministic SOURCE interpretation, not current access verification.

No facility is promoted to a destination. No opening time, entry coordinate,
price or dwell-time value is invented. Display repairs never touch source text.
"""
from __future__ import annotations

import re
from app.stage1.normalise import text, normal_name

# Exact CLASS mappings, with an explicit unknown fallback. These are project
# display groupings, not an official classification or a quality/popularity rank.
FACILITY_GROUPS = {
    'ACCESS POINT': ('access', 'Access point · unverified'),
    'FITNESS AREA': ('activity', 'Fitness area'),
    'PLAYGROUND': ('activity', 'Playground'),
    'FOOT RELAX': ('activity', 'Foot relaxation facility'),
    'LOOKOUT POINT': ('activity', 'Lookout point'),
    'BBQ PIT': ('activity', 'Barbecue pit'),
    'MULTIPURPOSE COURT': ('activity', 'Multipurpose court'),
    'ALLOTMENT GARDEN': ('activity', 'Allotment garden'),
    'DOG-AREA': ('activity', 'Dog area'),
    'THERAPEUTIC GARDEN': ('activity', 'Therapeutic garden facility'),
    'AMPHITHEATRE': ('activity', 'Amphitheatre facility'),
    'CAMPSITE': ('activity', 'Campsite facility'),
    'TOILET': ('support', 'Toilet'),
    'SHELTER': ('support', 'Shelter'),
    'DRINKING FOUNTAIN': ('support', 'Drinking fountain'),
    'BABY': ('support', 'Baby-care facility · subtype unverified'),
    'CARPARK': ('transport', 'Car park record'),
    'JETTY': ('transport', 'Jetty facility'),
    'BICYCLE RENTAL SHOP': ('transport', 'Bicycle rental facility'),
    'BICYCLE PARKING': ('transport', 'Bicycle parking facility'),
    'DROP-OFF/PICK-UP POINT': ('transport', 'Drop-off / pick-up point'),
    'NOTICEBOARD': ('information', 'Noticeboard'),
    'MAPBOARD': ('information', 'Map board'),
    'F&B': ('food', 'Food and beverage facility'),
}
GROUP_LABELS = {'access': 'Access points', 'activity': 'Activity facilities', 'support': 'Amenities',
                'transport': 'Transport facilities', 'information': 'Information facilities', 'food': 'Food facilities',
                'unknown': 'Unclassified'}
TRACK_LABELS = {'Footpath': 'Footpath', 'Staircase': 'Staircase', 'Cycle Track': 'Cycle track',
                'Service Track/Road': 'Service track / road', 'Boardwalk/Trail': 'Boardwalk / trail',
                'Underpass': 'Underpass', 'Pedestrian Overhead Bridge': 'Pedestrian overhead bridge',
                'Bridge/Drain Crossing': 'Bridge / drain crossing'}

# Search-only aliases preserve matching against the former Chinese display
# labels. They do not change classification or translate source place names.
FACILITY_SEARCH_ALIASES = {
    'ACCESS POINT': '通行接入点 · 待核验', 'FITNESS AREA': '健身区域',
    'PLAYGROUND': '游乐设施', 'FOOT RELAX': '足部放松设施', 'LOOKOUT POINT': '观景点',
    'BBQ PIT': '烧烤设施', 'MULTIPURPOSE COURT': '多用途球场', 'ALLOTMENT GARDEN': '分配园圃',
    'DOG-AREA': '犬只活动区域', 'THERAPEUTIC GARDEN': '疗愈花园设施',
    'AMPHITHEATRE': '露天剧场设施', 'CAMPSITE': '露营设施', 'TOILET': '洗手间',
    'SHELTER': '遮蔽设施', 'DRINKING FOUNTAIN': '饮水设施', 'BABY': '婴幼儿配套 · 细类待核验',
    'CARPARK': '停车相关记录', 'JETTY': '码头设施', 'BICYCLE RENTAL SHOP': '自行车租赁设施',
    'BICYCLE PARKING': '自行车停放设施', 'DROP-OFF/PICK-UP POINT': '接送点',
    'NOTICEBOARD': '告示牌', 'MAPBOARD': '导览地图牌', 'F&B': '餐饮设施',
}
GROUP_SEARCH_ALIASES = {'access': '通行接入点', 'activity': '活动设施', 'support': '生活配套',
                        'transport': '交通配套', 'information': '信息设施', 'food': '餐饮设施',
                        'unknown': '未识别类别'}
TRACK_SEARCH_ALIASES = {'Footpath': '步行道', 'Staircase': '楼梯', 'Cycle Track': '骑行道',
                        'Service Track/Road': '服务道路', 'Boardwalk/Trail': '栈道 / 步道',
                        'Underpass': '地下通道', 'Pedestrian Overhead Bridge': '行人天桥',
                        'Bridge/Drain Crossing': '桥梁 / 排水跨越'}

# Conservative exact substitutions only; no whole-string guessed re-encoding.
# The before/after substitutions are recorded, not presented as checked facts.
DISPLAY_REPAIRS = {'â€“': '–', 'â€”': '—', 'â€™': '’', 'â€˜': '‘',
                   'â€œ': '“', 'â€\x9d': '”', 'Â ': ' '}


def clean_enum(value):
    value = text(value)
    if value is None:
        return None
    # This handles quoted actual properties; report distribution keys are JSON
    # renderings and are never used as source property values by the pipeline.
    if len(value) > 1 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1].strip()
    return value or None


def flag(value):
    value = (clean_enum(value) or '').upper()
    return {'Y': 'source_yes', 'N': 'source_no'}.get(value, 'unknown')


def display_text(value):
    value = text(value)
    if value is None:
        return None, []
    changes = []
    for old, new in DISPLAY_REPAIRS.items():
        if old in value:
            changes.append({'before': old, 'after': new, 'count': value.count(old)})
            value = value.replace(old, new)
    return re.sub(r'\s+', ' ', value).strip(), changes


def interpret(record: dict, raw_feature: dict) -> dict:
    p = raw_feature.get('properties')
    if not isinstance(p, dict):
        p = {}
    key = record['source_key']
    role = record['role']
    is_place = role == 'candidate_places'
    repairs = {}
    shown = {}
    for name in ('name', 'display_title', 'description', 'opening_hours_raw'):
        shown[name], changes = display_text(record.get(name))
        if changes:
            repairs[name] = changes
    status = clean_enum(p.get('STATUS'))
    warnings = []
    review_hold = False
    if key == 'nea_hawker_centres':
        # Historical label only: never assert it is under construction TODAY.
        if (status or '').casefold() == 'under construction':
            review_hold = True
            warnings.append('source_under_construction_requires_current_status_check')
        elif (status or '').casefold() not in {'existing', 'existing (new)',
                                              'existing (replacement)', 'interim centre'}:
            warnings.append('source_status_missing_or_unrecognised')
    group = None
    facility_class = None
    search_aliases = []
    if key == 'nparks_facilities':
        facility_class = (clean_enum(p.get('CLASS')) or '').upper() or None
        group, label = FACILITY_GROUPS.get(facility_class, ('unknown', 'Facility type unverified'))
        search_aliases.append(GROUP_SEARCH_ALIASES[group])
        if group == 'unknown':
            warnings.append('facility_class_missing_or_unrecognised')
        # Do not invent a proper name for an unnamed facility.
        if not shown['name']:
            suffix = str(record['source_record_id'])[-12:]
            shown['display_title'] = f'{label} · Record {suffix}'
            old_label = FACILITY_SEARCH_ALIASES.get(facility_class, '设施类别待核验')
            search_aliases.append(f'{old_label} · 记录 {suffix}')
    if key == 'nparks_tracks':
        track_type = clean_enum(p.get('TYPE'))
        park = text(p.get('PARK'))
        shown['display_title'] = f"{TRACK_LABELS.get(track_type, 'Trail segment')} · {park or record['source_record_id']}"
        search_aliases.append(TRACK_SEARCH_ALIASES.get(track_type, '步道片段'))
        permission = {field.lower(): flag(p.get(field)) for field in
                      ('ALLOW_WALKING', 'ALLOW_CYCLING', 'ALLOW_PMD', 'ALLOW_WHEELING')}
    else:
        permission = {}
    tags = {'stb_attractions': ['景点候选', 'tourism'],
            'nparks_parks': ['公园', '自然', 'park', 'nature'],
            'nhb_historic_sites': ['历史地点', '历史', 'heritage'],
            'nhb_monuments': ['古迹', '建筑', 'monument'],
            'nea_hawker_centres': ['熟食中心', '美食', 'food']}.get(key, [])
    # These help CATEGORY search in Chinese. They are NOT translated place names,
    # preference scores, outdoor-access guarantees, or model-produced labels.
    e = {
        'record_uid': record['record_uid'],
        'display_title': shown['display_title'], 'display_description': shown['description'],
        'opening_hours_display': shown['opening_hours_raw'],
        'display_repairs': repairs, 'name_aliases': [],
        'search_tags': tags, 'tag_basis': 'project_source_category_dictionary_v1',
        'facility_class': facility_class, 'facility_group': group,
        'facility_group_label': GROUP_LABELS.get(group),
        'source_status_interpreted': status,
        'review_hold': review_hold,
        'default_exploration_visible': bool(is_place and record['map_eligible'] and not review_hold),
        'hold_is_not_current_closure_confirmation': review_hold,
        'warnings': warnings,
        'source_permissions': permission,
        'is_staircase_source': key == 'nparks_tracks' and clean_enum(p.get('TYPE')) == 'Staircase',
        'coordinate_role': ('source_access_point_not_verified_entrance'
                            if facility_class == 'ACCESS POINT' else record['coordinate_kind']),
        'park_boundary_candidates': [], 'access_boundary_candidates': [],
        'source_period_label': record.get('provenance', {}).get('source_period_label'),
        'capabilities': {'map_display': bool(record['map_eligible']),
                         'destination_exploration': bool(is_place and record['map_eligible'] and not review_hold),
                         'verified_entrance_routing': False,
                         'opening_window_validation': False,
                         'verified_dwell_time': False},
        'verified_entrances': [], 'planning_ready': False,
    }
    if is_place:
        e['warnings'] += ['entrance_unverified', 'current_opening_hours_unverified', 'dwell_time_unknown']
    e['search_text'] = normal_name(' '.join(str(v or '') for v in
        (shown['name'], shown['display_title'], shown['description'], record.get('address'),
         ' '.join(tags), facility_class, e['facility_group_label'], ' '.join(search_aliases))))
    return e
