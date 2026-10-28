"""English presentation of the curated catalogue without rewriting source data.

The dictionaries contain editorial translations, not verified official names.
Search keeps original aliases. Opening-hour parsing must use the original
schedule; apply english_view only after evaluating a plan or for display APIs.
"""
from __future__ import annotations

from functools import lru_cache
from copy import deepcopy
import json
import re

from app.config import ROOT

UNOFFICIAL = ' (unofficial translation)'
SOURCE_SCRIPT = re.compile(r'[\u0400-\u052f\u0600-\u06ff\u0e00-\u0e7f\u1000-\u109f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]')


@lru_cache(maxsize=1)
def _translations():
    def read(name):
        return json.loads((ROOT / 'config' / name).read_text(encoding='utf-8'))
    return read('english_place_names.json'), read('english_catalogue_text.json')


@lru_cache(maxsize=1)
def _name_pattern():
    names, _ = _translations()
    return re.compile('|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True)))


def english_name(value):
    """Use a recorded translation and visibly identify it as unofficial."""
    if not isinstance(value, str):
        return value
    names, text = _translations()
    translated = names.get(value)
    if translated:
        return translated + UNOFFICIAL
    if not SOURCE_SCRIPT.search(value):
        return value
    translated = text.get(value)
    return translated + UNOFFICIAL if translated else english_text(value)


def english_text(value):
    """Translate known catalogue text; never silently discard unknown content."""
    if not isinstance(value, str) or not SOURCE_SCRIPT.search(value):
        return value
    names, text = _translations()
    if value in text:
        return text[value]
    if value in names:
        return names[value] + UNOFFICIAL
    # Older snapshots contain generated labels. Translate only their fixed
    # labels; the original record suffix or containing-place text stays intact.
    generated = re.fullmatch(r'(.+) · 记录 (.*)', value, flags=re.S)
    if generated and generated[1] in text:
        return text[generated[1]] + ' · Record ' + generated[2]
    generated = re.fullmatch(
        r'(步道片段|未命名记录|步行道|楼梯|骑行道|服务道路|栈道 / 步道|地下通道|行人天桥|桥梁 / 排水跨越) · (.*)',
        value, flags=re.S)
    if generated and generated[1] in text:
        return text[generated[1]] + ' · ' + generated[2]
    # This provenance suffix is not a statement that the place is open.
    value = value.replace('（OSM 参考）', ' (OSM reference)')
    # Translate schedule notation for display only, including valid schedules
    # supplied by future catalogue snapshots rather than only the current map.
    for source, target in {'每日': 'Daily ', '次日': 'next day ',
                           '周一': 'Monday', '周二': 'Tuesday', '周三': 'Wednesday',
                           '周四': 'Thursday', '周五': 'Friday', '周六': 'Saturday',
                           '周日': 'Sunday', '闭馆': 'closed', '维护': 'maintenance',
                           '全天开放': 'Open all day', '24 小时': '24 hours',
                           '24小时': '24 hours', '；': '; ', '、': ', '}.items():
        value = value.replace(source, target)
    # Generated review notes contain a category and a machine-readable reason.
    value = re.sub(r'Overture 类别疑似错误：标为 (.+?)，但名称不像这类地点（(.+?)）',
                   r'Overture category/name flagged: \1; reason: \2', value)
    value = re.sub(r'Overture 重复记录：与 (.+?) 同名且相距 (\d+) 米以内',
                   r'Possible Overture duplicate: same name as \1 and within \2 metres', value)
    value = value.replace('与核心景点重复：', 'Duplicate of a core attraction: ')
    value = _name_pattern().sub(lambda match: names[match.group()] + UNOFFICIAL, value)
    return value


def english_view(value, *, _key=None):
    """Return a presentation copy while retaining raw queries and evidence quotes."""
    if _key in {'search_text', 'search_tags', 'quote', 'raw_source_feature',
                'raw_source_record', 'user_message', 'source_aliases'}:
        return deepcopy(value)
    if isinstance(value, dict):
        return {key: english_view(item, _key=key) for key, item in value.items()}
    if isinstance(value, list):
        return [english_view(item, _key=_key) for item in value]
    if _key in {'name', 'label', 'display_title', 'title', 'aliases', 'access_name', 'boundary_name'}:
        return english_name(value)
    return english_text(value)


def catalogue_search_row(entity):
    """Add English names for display and matching; preserve all source search text."""
    original = entity.get('display_title', '')
    translated = english_name(original)
    if translated == original:
        return entity
    row = dict(entity)
    row['display_title'] = translated
    names, _ = _translations()
    plain = names.get(original, translated.removesuffix(UNOFFICIAL))
    row['aliases'] = list(dict.fromkeys([*entity.get('aliases', []), original, plain]))
    row['search_text'] = ' '.join([entity.get('search_text', ''), original, plain]).casefold()
    row['name_translation_status'] = 'unofficial'
    return row
