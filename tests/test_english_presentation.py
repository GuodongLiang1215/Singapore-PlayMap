"""English display copies preserve multilingual search and raw scheduling evidence."""
from copy import deepcopy
from datetime import datetime
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.english import catalogue_search_row, english_name, english_text, english_view
from app.stage1c import api as catalogue
from app.stage2b import planning
from app.stage2b.models import PlanRequest, PlacePoint, TimeSettings, Visit
from app.stage2b.opening_hours import evaluate_visit_hours
from app.stage2c.catalogue import LocalCatalogue
from app.stage2c.models import PlaceSpec, Preferences


SOURCE_NAME = 'Blk 501 金味咖啡店'
ENGLISH_NAME = 'Blk 501 Jin Wei Coffee Shop'
DISPLAY_NAME = ENGLISH_NAME + ' (unofficial translation)'
SOURCE_NOTE = '坐标来自 Overture，建议在地图上看一眼'
ENGLISH_NOTE = 'Coordinates come from Overture; review the location on the map'
SOURCE_HOURS = '每日 9:00–18:00'


def source_entity(hours=SOURCE_HOURS):
    return {
        'entity_id': 'coffee-1', 'primary_uid': 'fixture:coffee-1',
        'display_title': SOURCE_NAME, 'aliases': ['金味咖啡店'],
        'search_text': SOURCE_NAME.casefold(), 'search_tags': ['coffee'],
        'category': 'food', 'categories': ['food'], 'address': '501 Example Road',
        'latitude': 1.31, 'longitude': 103.86,
        'map_eligible': True, 'default_exploration_visible': True,
        'regular_hours_evidence': [], 'legacy_reviews': [],
        'opening_hours_display': hours, 'suggested_duration': None,
        'review_notes': [SOURCE_NOTE], 'member_count': 1,
        'overture': {'category': 'coffee_shop'},
    }


def test_known_names_are_labelled_without_changing_english_names_or_unknown_content():
    assert english_name(SOURCE_NAME) == DISPLAY_NAME
    assert english_name('Merlion Park') == 'Merlion Park'
    assert english_name(DISPLAY_NAME) == DISPLAY_NAME
    assert english_text(SOURCE_NOTE) == ENGLISH_NOTE
    assert english_text('Visit ' + SOURCE_NAME + ' next.') == 'Visit ' + DISPLAY_NAME + ' next.'
    assert english_text('未登记的原文') == '未登记的原文'


def test_presentation_is_a_deep_copy_including_preserved_original_evidence():
    source = {
        'entity': source_entity(),
        'quote': '请加一个公园', 'user_message': '我想去金味咖啡店',
        'raw_source_feature': {'name': SOURCE_NAME, 'values': [SOURCE_NOTE]},
        'source_aliases': [SOURCE_NAME],
    }
    before = deepcopy(source)
    shown = english_view(source)
    assert shown['entity']['display_title'] == DISPLAY_NAME
    assert shown['entity']['opening_hours_display'] == 'Daily 09:00–18:00'
    assert shown['entity']['review_notes'] == [ENGLISH_NOTE]
    for key in ('quote', 'user_message', 'raw_source_feature', 'source_aliases'):
        assert shown[key] == source[key]
    shown['entity']['review_notes'].append('A display-only note')
    shown['entity']['search_tags'].append('display-only-tag')
    shown['raw_source_feature']['values'].append('display-only-value')
    shown['source_aliases'].append('display-only-alias')
    assert source == before


def test_search_projection_retains_source_terms_and_adds_plain_english_name():
    source = source_entity()
    before = deepcopy(source)
    row = catalogue_search_row(source)
    assert row['display_title'] == DISPLAY_NAME
    assert {SOURCE_NAME, ENGLISH_NAME, '金味咖啡店'} <= set(row['aliases'])
    assert SOURCE_NAME.casefold() in row['search_text']
    assert ENGLISH_NAME.casefold() in row['search_text']
    assert row['opening_hours_display'] == SOURCE_HOURS
    assert source == before


@pytest.fixture
def catalogue_db(tmp_path, monkeypatch):
    path = tmp_path / 'catalogue.sqlite'
    source = source_entity()
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE entities (
                entity_id TEXT, title TEXT, search_text TEXT,
                visible INTEGER, map_eligible INTEGER, data_json TEXT);
            CREATE TABLE source_records (record_uid TEXT, data_json TEXT);
            CREATE TABLE members (entity_id TEXT, record_uid TEXT);
        ''')
        db.execute('INSERT INTO entities VALUES (?, ?, ?, 1, 1, ?)',
                   (source['entity_id'], SOURCE_NAME, source['search_text'],
                    json.dumps(source, ensure_ascii=False)))
        db.execute('INSERT INTO source_records VALUES (?, ?)',
                   ('fixture:coffee-1', json.dumps({'name': SOURCE_NAME,
                                                   'notes': SOURCE_NOTE}, ensure_ascii=False)))
        db.execute('INSERT INTO members VALUES (?, ?)', ('coffee-1', 'fixture:coffee-1'))
    report = {'build_id': 'english-presentation-fixture'}
    monkeypatch.setattr(catalogue, 'snapshot', lambda build_id=None: (path, report))
    # Avoid reusing nearby/search indexes from another test or a real catalogue.
    if hasattr(catalogue, '_NEARBY_CACHE'):
        monkeypatch.setattr(catalogue, '_NEARBY_CACHE', {'build_id': None, 'rows': []})
    return path, source, report


@pytest.mark.parametrize('query', [SOURCE_NAME, ENGLISH_NAME])
def test_local_retrieval_finds_both_names_as_the_same_exact_identity(catalogue_db, query):
    cat = LocalCatalogue()
    candidates, _notices, held = cat.search(PlaceSpec(query=query), Preferences(), None, set())
    assert held is False
    assert len(candidates) == 1
    assert candidates[0]['identity'] == 'catalogue:coffee-1'
    assert candidates[0]['label'] == DISPLAY_NAME
    # An existing exact alias must not turn into an unnecessary geocoder fallback.
    assert candidates[0]['exact_name_match'] is True


def test_feature_and_detail_http_views_are_english_but_internal_entity_stays_raw(catalogue_db):
    path, before, report = catalogue_db
    app = FastAPI()
    app.include_router(catalogue.router)
    with TestClient(app) as client:
        for query in (SOURCE_NAME, ENGLISH_NAME):
            response = client.get('/api/stage1c/features', params={'q': query, 'view': 'all'})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data['matched_total'] == 1
            assert data['features'][0]['id'] == 'coffee-1'
            props = data['features'][0]['properties']
            assert props['display_title'] == DISPLAY_NAME
            assert props['opening_hours_display'] == 'Daily 09:00–18:00'
        response = client.get('/api/stage1c/entity', params={'entity_id': 'coffee-1'})
        assert response.status_code == 200, response.text
        shown = response.json()
        assert shown['entity']['display_title'] == DISPLAY_NAME
        assert shown['entity']['review_notes'] == [ENGLISH_NOTE]
        assert shown['source_records'][0]['name'] == DISPLAY_NAME
        assert shown['source_records'][0]['notes'] == ENGLISH_NOTE
    raw = catalogue.entity('coffee-1', report['build_id'])['entity']
    assert raw['display_title'] == SOURCE_NAME
    assert raw['opening_hours_display'] == SOURCE_HOURS
    assert raw['review_notes'] == [SOURCE_NOTE]
    with sqlite3.connect(path) as db:
        stored = json.loads(db.execute('SELECT data_json FROM entities').fetchone()[0])
    assert stored == before


@pytest.mark.parametrize(('hours', 'expected_status'), [
    (SOURCE_HOURS, 'closed_at_arrival'),
    ('24 小时', 'feasible'),
    ('每日 10:00–19:00；周五至 21:00', 'unknown'),
])
def test_plan_checks_raw_hours_before_english_presentation(hours, expected_status):
    source = source_entity(hours)
    before = deepcopy(source)
    origin = PlacePoint(latitude=1.30, longitude=103.85, label='Origin',
                        source='user_map', confirmed=True)
    point = PlacePoint(latitude=1.31, longitude=103.86, label=DISPLAY_NAME,
                       source='catalogue_representative', confirmed=True,
                       entity_id='coffee-1', catalogue_build_id='english-presentation-fixture')
    request = PlanRequest(
        session_id='f' * 32, origin=origin,
        visits=[Visit(visit_id='v1', point=point, stay_minutes=30)], mode='walk',
        time=TimeSettings(mode='estimate', departure_at=datetime.fromisoformat('2026-10-30T20:00:00+08:00')),
    )
    prepared = planning.prepare(request, lambda uid, build: {'entity': source})
    assert prepared['visits'][0]['opening_hours_display'] == hours
    leg = {
        'leg_id': 'leg-1', 'index': 0, 'origin': origin.model_dump(),
        'destination': point.model_dump(), 'mode': 'walk', 'kind': 'provider_route',
        'distance_m': 700, 'duration_s': 600,
        'geometry': {'type': 'LineString', 'coordinates': [[103.85, 1.30], [103.86, 1.31]]},
        'endpoint_review_required': False,
        'diagnostics': {'large_detour_flag': False, 'geometry_summary_mismatch': False},
    }
    shown = planning.assemble(request, prepared, [leg])
    visit = shown['visits'][0]
    raw_check = evaluate_visit_hours(arrival_at=visit['arrival_at'],
                                     departure_at=visit['departure_at'], opening_hours_display=hours)
    assert raw_check['status'] == expected_status
    assert visit['opening_status'] == expected_status
    assert visit['opening_hours_check'] == english_view(raw_check)
    assert visit['opening_hours_display'] == english_text(hours)
    assert visit['point']['label'] == DISPLAY_NAME
    assert shown['itinerary_feasible'] is (
        None if expected_status == 'unknown' else expected_status == 'feasible')
    assert source == before
