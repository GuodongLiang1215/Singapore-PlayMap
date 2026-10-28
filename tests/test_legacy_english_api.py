"""Legacy snapshot display projection preserves stored queries and evidence."""
import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.english import english_text
from app.stage1 import api as stage1
from app.stage1b import api as stage1b


@pytest.mark.parametrize(('label', 'expected'), [
    ('步道片段 · Example Park', 'Trail segment · Example Park'),
    ('楼梯 · Example Park', 'Staircase · Example Park'),
    ('未命名记录 · 0012/03', 'Unnamed record · 0012/03'),
    ('洗手间 · 记录 0012/03', 'Toilet · Record 0012/03'),
    ('通行接入点 · 待核验 · 记录 0012', 'Access point · unverified · Record 0012'),
    ('设施类别待核验 · 记录 0012', 'Facility type unverified · Record 0012'),
    # A generated record suffix is opaque, even if it happens to match a name.
    ('未命名记录 · Blk 501 金味咖啡店', 'Unnamed record · Blk 501 金味咖啡店'),
    ('新标签 · 记录 0012', '新标签 · 记录 0012'),
])
def test_generated_labels_translate_only_known_prefixes(label, expected):
    assert english_text(label) == expected


@pytest.fixture
def legacy_snapshot(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.sqlite'
    record = {
        'record_uid': 'nparks_facilities:0012', 'source_key': 'nparks_facilities',
        'role': 'amenity_layer', 'category': 'amenity_layer', 'name': None,
        'display_title': '未命名记录 · 0012', 'address': None,
        'longitude': 103.85, 'latitude': 1.3, 'coordinate_kind': 'source_point',
        'opening_verification': 'unknown', 'access_status': 'unknown',
        'planning_ready': False, 'data_issues': [],
        'geometry': {'type': 'Point', 'coordinates': [103.85, 1.3]},
    }
    raw = {'type': 'Feature', 'properties': {'NAME': '原始名称', 'CLASS': 'TOILET'},
           'geometry': record['geometry']}
    enrichment = {
        'display_title': '洗手间 · 记录 0012', 'search_tags': ['洗手间'],
        'search_text': '洗手间 生活配套 0012', 'facility_class': 'TOILET',
        'facility_group': 'support', 'facility_group_label': '生活配套',
        'source_status_interpreted': None, 'source_period_label': '2025',
        'review_hold': False, 'default_exploration_visible': False,
        'coordinate_role': 'source_point', 'source_permissions': {},
        'is_staircase_source': False, 'access_boundary_candidates': [],
    }
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE features (record_uid TEXT, source_key TEXT, source_row INTEGER,
                role TEXT, category TEXT, map_eligible INTEGER, search_text TEXT,
                normalised_json TEXT, raw_feature_json TEXT);
            CREATE TABLE enrichment (record_uid TEXT, facility_class TEXT, facility_group TEXT,
                default_exploration_visible INTEGER, search_text TEXT, data_json TEXT);
        ''')
        db.execute('INSERT INTO features VALUES (?, ?, 1, ?, ?, 1, ?, ?, ?)', (
            record['record_uid'], record['source_key'], record['role'], record['category'],
            enrichment['search_text'], json.dumps(record, ensure_ascii=False), json.dumps(raw, ensure_ascii=False)))
        db.execute('INSERT INTO enrichment VALUES (?, ?, ?, 0, ?, ?)', (
            record['record_uid'], 'TOILET', 'support', enrichment['search_text'],
            json.dumps(enrichment, ensure_ascii=False)))
    report = {'build_id': 'legacy-english-fixture', 'duplicate_pairs': []}
    monkeypatch.setattr(stage1, 'snapshot', lambda: (path, report))
    monkeypatch.setattr(stage1b, 'snapshot', lambda build_id=None: (path, report))
    return path, record, raw, enrichment


@pytest.mark.parametrize(('api', 'prefix'), [(stage1, '/api/stage1'), (stage1b, '/api/stage1b')])
def test_legacy_http_views_translate_without_changing_snapshot_or_query(api, prefix, legacy_snapshot):
    path, record, raw, enrichment = legacy_snapshot
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        response = client.get(prefix + '/features', params={'layer': 'facilities', 'q': '洗手间'})
        assert response.status_code == 200
        result = response.json()
        assert result['matched_total'] == 1 and result['next_offset'] is None
        feature = result['features'][0]
        assert feature['id'] == record['record_uid']
        assert feature['geometry'] == record['geometry']
        props = feature['properties']
        assert props['record_uid'] == record['record_uid']
        assert props['longitude'] == 103.85 and props['latitude'] == 1.3
        assert props['display_title'] == ('Unnamed record · 0012' if api is stage1 else 'Toilet · Record 0012')
        if api is stage1b:
            assert props['facility_group_label'] == 'Amenities'
            assert props['search_text'] == enrichment['search_text']
            assert props['search_tags'] == enrichment['search_tags']
        # Presentation translation must not change the legacy SQL query rules.
        assert client.get(prefix + '/features', params={'layer': 'facilities', 'q': 'Toilet'}).json()['matched_total'] == 0
        detail = client.get(prefix + '/record', params={'uid': record['record_uid']})
        assert detail.status_code == 200
        shown = detail.json()
        assert shown['raw_source_feature'] == raw
        assert shown['record']['display_title'] == 'Unnamed record · 0012'
        assert shown['record']['geometry'] == record['geometry']
        if api is stage1b:
            assert shown['enrichment']['display_title'] == 'Toilet · Record 0012'
            assert shown['candidate_links_verified'] is False
    with sqlite3.connect(path) as db:
        stored_record, stored_raw = db.execute('SELECT normalised_json,raw_feature_json FROM features').fetchone()
        stored_enrichment = db.execute('SELECT data_json FROM enrichment').fetchone()[0]
    assert json.loads(stored_record) == record
    assert json.loads(stored_raw) == raw
    assert json.loads(stored_enrichment) == enrichment
