"""A replacement must offer another place without weakening scoped type filters."""
from copy import deepcopy

import pytest

from app.stage2b.models import PlacePoint
from app.stage2c import catalogue as module
from app.stage2c.catalogue import LocalCatalogue, retrieve_slot
from app.stage2c.models import LocationAction, PlaceSpec, Preferences, SemanticSegment


def entity(uid, label, longitude=103.85, subtype='cafe'):
    return {'entity_id': uid, 'display_title': label, 'category': 'food',
            'categories': ['food'], 'overture': {'category': subtype},
            'address': '', 'search_tags': [], 'search_text': label,
            'latitude': 1.30, 'longitude': longitude, 'map_eligible': True,
            'default_exploration_visible': True}


def point(uid='old', longitude=103.85):
    return PlacePoint(latitude=1.30, longitude=longitude, label='Original cafe',
                      source='catalogue_representative' if uid else 'user_map', confirmed=True,
                      entity_id=uid, catalogue_build_id='replacement-candidates' if uid else None)


class NoGeo:
    def search(self, *args, **kwargs):
        raise AssertionError('This case must stay within the local catalogue')


class FakeGeo:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def search(self, query, page):
        self.calls.append((query, page))
        return {'results': deepcopy(self.rows), 'next_page': None}


def cafe_requirement():
    return SemanticSegment(type='constraint', name='place_type', value='cafe',
                           scope='visit:1', hardness='hard', quote='another cafe', effect='set')


def retrieve(rows, target, *, query='', existing=(), geo=None, hard_cafe=True, operation='replace_visit'):
    cat = LocalCatalogue(rows=rows, build_id='replacement-candidates')
    action = LocationAction(op=operation, target_id='coffee' if operation == 'replace_visit' else None,
                            place=PlaceSpec(query=query, categories=['food']), quote='another cafe')
    return retrieve_slot(0, action, cat, Preferences(), point(None, 103.84), set(existing),
                         geo or NoGeo(), requirements=[cafe_requirement()] if hard_cafe else [],
                         replacement_target=target)


@pytest.mark.parametrize('candidate,target,expected', [
    ({'entity_id': 'old', 'latitude': 1.31, 'longitude': 103.86}, point(), True),
    ({'entity_id': 'other', 'latitude': 1.30, 'longitude': 103.85}, point(), False),
    ({'entity_id': 'old', 'latitude': 1.30, 'longitude': 103.85}, point(None), True),
    ({'latitude': 1.30, 'longitude': 103.85}, point(), True),
    ({'latitude': 1.30, 'longitude': 103.85001}, point(), False),
    ({'label': 'Original cafe'}, point(), False),
    ({}, {}, False), ({'latitude': None, 'longitude': None}, {}, False),
    ({'latitude': float('nan'), 'longitude': 103.85}, point(None), False),
    ({'latitude': 1.30, 'longitude': 103.85}, None, False),
])
def test_replacement_identity_prefers_known_ids_and_uses_coordinates_only_as_fallback(candidate, target, expected):
    before_candidate, before_target = deepcopy(candidate), deepcopy(target)
    assert module.same_replacement_place(candidate, target) is expected
    if not isinstance(candidate.get('latitude'), float) or candidate['latitude'] == candidate['latitude']:
        assert candidate == before_candidate
    assert target == before_target


def test_helper_accepts_two_model_points_for_the_engine_guard():
    assert module.same_replacement_place(point(), point())
    assert not module.same_replacement_place(point('different'), point())


def test_generic_manual_target_excludes_same_coordinates_but_keeps_cafe_requirement():
    slot = retrieve([entity('old', 'Original cafe'), entity('other', 'Other cafe', 103.86),
                     entity('restaurant', 'AAA Restaurant', 103.851, 'restaurant')], point(None))
    assert [option['point']['entity_id'] for option in slot['options']] == ['other']
    assert slot['retrieval_diagnostic']['required_place_types'] == ['cafe']
    assert all(option['place_types'] == ['cafe'] for option in slot['options'])


def test_named_original_is_not_offered_as_its_own_replacement():
    slot = retrieve([entity('old', 'Original cafe'), entity('other', 'Other cafe', 103.86)],
                    point(), query='Original cafe', existing={'old'})
    assert slot['options'] == []
    assert slot['explicit_location_confirmation'] is True
    assert slot['retrieval_diagnostic']['geocoder_attempted'] is False


def test_named_geocoder_fallback_excludes_original_manual_coordinates_and_keeps_alternative():
    geo = FakeGeo([{'label': 'Original cafe', 'latitude': 1.30, 'longitude': 103.85},
                   {'label': 'Other branch', 'latitude': 1.31, 'longitude': 103.86}])
    slot = retrieve([], point(None), query='Original cafe', geo=geo, hard_cafe=False)
    assert [option['label'] for option in slot['options']] == ['Other branch']
    assert geo.calls == [('Original cafe', 1)]
    assert [option['candidate_key'] for option in slot['options']] == ['s0c0']


def test_explicitly_named_other_itinerary_visit_can_still_be_reused():
    slot = retrieve([entity('old', 'Original cafe'), entity('other', 'Other cafe', 103.86)],
                    point(), query='Other cafe', existing={'old', 'other'})
    assert [option['point']['entity_id'] for option in slot['options']] == ['other']


def test_generic_replacement_still_excludes_other_itinerary_visits():
    slot = retrieve([entity('old', 'Original cafe'), entity('other', 'Other cafe', 103.86),
                     entity('fresh', 'Fresh cafe', 103.87)], point(), existing={'old', 'other'})
    assert [option['point']['entity_id'] for option in slot['options']] == ['fresh']


def test_distinct_catalogue_cafe_at_same_coordinates_is_a_valid_alternative():
    slot = retrieve([entity('old', 'Original cafe'), entity('other', 'Other cafe')],
                    point(), existing={'old'})
    assert [option['point']['entity_id'] for option in slot['options']] == ['other']


def test_filter_runs_before_shortlist_limit():
    rows = [entity('old', 'A original cafe')]
    rows.extend(entity(f'new-{i}', f'Cafe {i}', 103.86 + i / 1000) for i in range(8))
    slot = retrieve(rows, point(None))
    assert len(slot['options']) == 8
    assert all(option['point']['entity_id'] != 'old' for option in slot['options'])


def test_endpoint_query_does_not_apply_replacement_exclusion():
    slot = retrieve([entity('old', 'Original cafe')], point(), query='Original cafe',
                    operation='set_origin', existing={'old'}, geo=FakeGeo([]), hard_cafe=False)
    assert [option['point']['entity_id'] for option in slot['options']] == ['old']
