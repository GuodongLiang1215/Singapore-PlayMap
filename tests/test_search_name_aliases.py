"""Name-only catalogue identity matching across original and English labels."""
from copy import deepcopy
import json
import sqlite3

import pytest

from app.stage1c import api as catalogue
from app.stage2b.models import PlacePoint
from app.stage2b.planning import resolve_point


# Names, coordinates and raw hours from the bounded real-catalogue audit.
CASES = [
    ('Dessert First 糖水先', 'Dessert First', 1.2978808, 103.8565598, 'food',
     'Mo-Fr 12:30-00:30; Sa 12:30-01:30; Su 12:00-00:30（OSM 参考）'),
    ('Singapore Chinese Cultural Centre 新加坡华族文化中心', 'Singapore Chinese Cultural Centre',
     1.2759887, 103.8495789, 'heritage', 'Mo-Su 10:00-20:00（OSM 参考）'),
    ('Great Nanyang Heritage Cafe 大南洋', 'Great Nanyang Heritage Cafe',
     1.2775436, 103.8430862, 'food', 'Mo-Su 07:30-20:00（OSM 参考）'),
    ('Zui Yu Xuan Teochew Cuisine 醉宇轩经典潮膳', 'Zui Yu Xuan Teochew Cuisine',
     1.2828578, 103.8480682, 'food', 'Mo-Su 11:30-15:00,18:00-23:00（OSM 参考）'),
]
UNOFFICIAL = ' (unofficial translation)'


def entity(index):
    name, _english, latitude, longitude, category, hours = CASES[index]
    return {
        'entity_id': f'place-{index}', 'display_title': name,
        'latitude': latitude, 'longitude': longitude,
        'category': category, 'categories': [category],
        'aliases': ['DF'] if index == 0 else [],
        'search_text': name.casefold() + ' food cafe shopping museum',
        'opening_hours_display': hours,
        'suggested_duration': {'short': 10, 'regular': 20, 'long': 30},
        'regular_hours_evidence': [], 'entrance_points': [],
        'default_exploration_visible': True,
    }


@pytest.fixture
def load_catalogue(tmp_path, monkeypatch):
    def load(rows):
        path = tmp_path / 'names.sqlite'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS entities '
                       '(visible INTEGER, map_eligible INTEGER, data_json TEXT)')
            db.execute('DELETE FROM entities')
            db.executemany('INSERT INTO entities VALUES (1, 1, ?)',
                           [(json.dumps(row, ensure_ascii=False),) for row in rows])
        monkeypatch.setattr(catalogue, 'snapshot', lambda build_id=None: (path, {'build_id': 'names-fixture'}))
        monkeypatch.setattr(catalogue, '_NEARBY_CACHE', {'build_id': None, 'rows': []})
        by_id = {row['entity_id']: row for row in rows}

        def resolver(uid, build):
            assert build == 'names-fixture'
            return {'entity': deepcopy(by_id[uid])}

        return resolver
    return load


@pytest.mark.parametrize('index', range(4))
@pytest.mark.parametrize('label_form', ['original', 'plain', 'marked'])
def test_same_point_inherits_same_metadata_for_original_and_english_names(load_catalogue, index, label_form):
    rows = [entity(i) for i in range(4)]
    before = deepcopy(rows)
    resolver = load_catalogue(rows)
    source = rows[index]
    original, english, latitude, longitude, _category, _hours = CASES[index]
    label = {'original': original, 'plain': english, 'marked': english + UNOFFICIAL}[label_form]
    point = PlacePoint(latitude=latitude, longitude=longitude, label=label,
                       source='onemap_search', confirmed=True)
    result = resolve_point(point, resolver, catalogue.match_search_point)
    assert result['source_checked'] is True
    assert result['catalogue_metadata_match']['entity_id'] == source['entity_id']
    assert result['catalogue_metadata_match']['name_score'] == 1.0
    assert result['category'] == source['category']
    assert result['suggested_duration'] == source['suggested_duration']
    assert result['opening_hours_display'] == source['opening_hours_display']
    assert result['point'] == point
    assert rows == before


def test_explicit_source_alias_matches_even_when_its_tokens_are_short(load_catalogue):
    source = entity(0)
    load_catalogue([source])
    result = catalogue.match_search_point('DF', source['latitude'], source['longitude'])
    assert result['entity_id'] == source['entity_id']
    assert result['name_score'] == 1.0


@pytest.mark.parametrize('label', ['Dessert First', 'Dessert First' + UNOFFICIAL])
def test_same_english_name_at_two_nearby_entities_remains_ambiguous(load_catalogue, label):
    source = entity(0)
    other = {**deepcopy(source), 'entity_id': 'other', 'display_title': 'Dessert First',
             'aliases': [], 'longitude': source['longitude'] + 0.0001}
    load_catalogue([source, other])
    assert catalogue.match_search_point(label, source['latitude'], source['longitude']) is None


@pytest.mark.parametrize('label', ['food', 'museum', 'unofficial translation'])
def test_search_keywords_and_translation_notice_are_not_identity_names(load_catalogue, label):
    source = entity(0)
    load_catalogue([source])
    assert catalogue.match_search_point(label, source['latitude'], source['longitude']) is None


@pytest.mark.parametrize(('entrances', 'latitude_offset', 'expected_scope'), [
    ([], 0.003, None),  # A translated name does not remove the close-radius guard.
    ([{'name': 'Gate', 'latitude': 1.30, 'longitude': 103.85}], 0.003, 'reviewed_large_destination'),
    ([{'name': 'Gate', 'latitude': 1.30, 'longitude': 103.85}], 0.06, None),
])
def test_translated_name_preserves_large_place_distance_and_entrance_guards(
        load_catalogue, entrances, latitude_offset, expected_scope):
    source = entity(0)
    source['entrance_points'] = entrances
    load_catalogue([source])
    result = catalogue.match_search_point('Dessert First' + UNOFFICIAL,
                                          source['latitude'] + latitude_offset, source['longitude'])
    if expected_scope is None:
        assert result is None
    else:
        assert result['entity_id'] == source['entity_id']
        assert result['match_scope'] == expected_scope


def test_internal_identity_aliases_are_not_added_to_nearby_presentation(load_catalogue):
    source = entity(0)
    load_catalogue([source])
    response = catalogue.nearby(lat=source['latitude'], lon=source['longitude'],
                                radius_m=1000, category='', q='', limit=20, build_id=None)
    assert len(response['places']) == 1
    assert response['places'][0]['display_title'] == 'Dessert First' + UNOFFICIAL
    assert 'name_aliases' not in response['places'][0]
