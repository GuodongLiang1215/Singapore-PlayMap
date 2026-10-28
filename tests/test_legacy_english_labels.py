"""English display labels must not remove existing Chinese category searches."""
import re

import pytest

from app.stage1b.rules import interpret


def source_record(key):
    return {
        'record_uid': key + ':12', 'source_key': key, 'source_record_id': '12',
        'role': 'amenity_layer', 'name': None, 'display_title': 'Unnamed record · 12',
        'coordinate_kind': 'source_point', 'map_eligible': True,
    }


@pytest.mark.parametrize(('facility_class', 'query'), [
    ('TOILET', '洗手间'), ('ACCESS POINT', '通行接入点'), ('OTHER', '未识别类别'),
])
def test_english_facility_display_preserves_existing_chinese_search(facility_class, query):
    result = interpret(source_record('nparks_facilities'), {'properties': {'CLASS': facility_class}})
    assert not re.search(r'[\u3400-\u9fff]', result['display_title'] + result['facility_group_label'])
    assert query in result['search_text']
    assert '记录' in result['search_text']
    assert result['search_tags'] == []
    assert result['verified_entrances'] == []
    assert result['planning_ready'] is False


@pytest.mark.parametrize(('track_type', 'query'), [('Staircase', '楼梯'), ('OTHER', '步道片段')])
def test_english_trail_display_preserves_search_and_unknown_access(track_type, query):
    result = interpret(source_record('nparks_tracks'), {'properties': {'TYPE': track_type, 'PARK': 'Example Park'}})
    assert not re.search(r'[\u3400-\u9fff]', result['display_title'])
    assert query in result['search_text']
    assert 'example park' in result['search_text']
    assert set(result['source_permissions'].values()) == {'unknown'}
    assert result['planning_ready'] is False
