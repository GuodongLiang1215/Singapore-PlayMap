import pytest
from types import SimpleNamespace

from app.stage2c.catalogue import LocalCatalogue, retrieve_slot
from app.stage2c.models import LocationAction, PlaceSpec, Preferences, SemanticSegment
from app.stage2c.retrieval_rules import SUPPORTED_PLACE_TYPES, canonical_place_type
from app.stage2a.transport import ProviderError


def catalogue_place(entity_id, title, category, subtype=None, tags=()):
    row = {
        'entity_id': entity_id,
        'display_title': title,
        'categories': [category],
        'category': category,
        'address': '',
        'search_tags': list(tags),
        'search_text': ' '.join([title, *tags]),
        'latitude': 1.30,
        'longitude': 103.85,
        'map_eligible': True,
        'default_exploration_visible': True,
    }
    if subtype is not None:
        row['overture'] = {'category': subtype}
    return row


def test_park_discovery_keeps_the_fine_type_instead_of_all_nature():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('park', 'City Park', 'nature', 'park'),
        catalogue_place('beach', 'Park Beach', 'nature', 'beach'),
        catalogue_place('garden', 'Park Garden', 'nature', 'garden'),
    ])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['nature'], keywords=['park']), Preferences(), None, set()
    )

    assert [option['identity'] for option in options] == ['catalogue:park']


class NoGeocoder:
    def search(self, *args):
        raise AssertionError('Generic discovery must use catalogue types')


def test_generic_cafe_query_discovers_coffee_shop_without_geocoding():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('cafe', 'Morning Cup', 'food', 'coffee_shop'),
        catalogue_place('hawker', 'Cafe Food Centre', 'food', 'hawker_centre'),
    ])
    action = LocationAction(op='add_visit', place=PlaceSpec(query='咖啡馆'), quote='咖啡馆')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), NoGeocoder())

    assert slot['query'] == ''
    assert slot['categories'] == ['food']
    assert [option['identity'] for option in slot['options']] == ['catalogue:cafe']


def requirement(name, value, hardness='hard'):
    return SemanticSegment(type='constraint', name=name, value=value, scope='visit:new',
                           hardness=hardness, quote=str(value), effect='set')


def test_hard_types_are_conjoined_instead_of_keyword_or():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('cafe', 'Cup', 'food', tags=['cafe']),
        catalogue_place('restaurant', 'Dinner', 'food', tags=['restaurant']),
        catalogue_place('both', 'Bistro', 'food', tags=['cafe', 'restaurant']),
    ])
    action = LocationAction(op='add_visit', place=PlaceSpec(categories=['food']), quote='cafe restaurant')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), NoGeocoder(),
                         requirements=[requirement('place_type', 'cafe'),
                                       requirement('place_type', 'restaurant')])

    assert [option['identity'] for option in slot['options']] == ['catalogue:both']


def test_geocoder_cannot_supply_an_unknown_hard_place_type():
    catalogue = LocalCatalogue(rows=[catalogue_place('beach', 'Park View', 'nature', 'beach')])
    action = LocationAction(op='add_visit', place=PlaceSpec(query='Park View'), quote='Park View')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), NoGeocoder(),
                         requirements=[requirement('place_type', 'park')])

    assert slot['options'] == []
    assert slot['retrieval_diagnostic']['geocoder_attempted'] is False
    assert slot['notices']


@pytest.mark.parametrize(('place_type', 'category', 'subtype'), [
    ('restaurant', 'food', 'japanese_restaurant'),
    ('museum', 'tourism', 'science_museum'),
    ('park', 'nature', 'national_park'),
])
def test_structured_source_subtypes_establish_the_supported_type(place_type, category, subtype):
    catalogue = LocalCatalogue(rows=[catalogue_place('source', 'Venue', category, subtype)])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=[category], keywords=[place_type]), Preferences(), None, set()
    )

    assert [option['identity'] for option in options] == ['catalogue:source']


def test_excluding_parks_does_not_exclude_all_nature():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('park', 'City Park', 'nature', 'park'),
        catalogue_place('beach', 'Park Beach', 'nature', 'beach'),
    ])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['nature']), Preferences(excluded=['park']), None, set()
    )

    assert [option['identity'] for option in options] == ['catalogue:beach']


def test_unknown_one_map_type_still_applies_name_based_exclusions():
    from app.stage2c.catalogue import blocked

    assert blocked({'display_title': 'National Museum', 'categories': [],
                    'search_tags': []}, ['museum']) is True


def test_known_cafe_type_is_not_excluded_by_the_word_museum_in_its_name():
    from app.stage2c.catalogue import blocked

    row = catalogue_place('cafe', 'Museum Café', 'food', 'cafe')

    assert blocked(row, ['museum']) is False


def test_explicit_official_park_source_supplies_type_evidence():
    row = catalogue_place('official', 'Official Open Space', 'nature')
    row['primary_uid'] = 'nparks_parks:123'
    catalogue = LocalCatalogue(rows=[row])

    options, _, _ = catalogue.search(
        PlaceSpec(keywords=['公园']), Preferences(), None, set()
    )

    assert options[0]['place_types'] == ['park']
    assert options[0]['place_type_basis'] == 'source:nparks_parks'


def test_structured_soft_type_ranks_without_becoming_a_hard_keyword_filter():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('hawker', 'A Food Centre', 'food', 'hawker_centre'),
        catalogue_place('cafe', 'Z Morning Cup', 'food', 'cafe'),
    ])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['food'], keywords=['cafe']), Preferences(), None, set(),
        requirements=[requirement('place_type', 'cafe', hardness='soft')]
    )

    assert [option['identity'] for option in options] == ['catalogue:cafe', 'catalogue:hawker']


@pytest.mark.parametrize('subtype', ['beach', 'amusement_park', 'science_park'])
def test_structured_nonpark_category_is_not_overridden_by_title_or_tags(subtype):
    row = catalogue_place('wrong', 'Park', 'nature', subtype, tags=['公园', 'park'])
    row['address'] = 'Park Road'
    catalogue = LocalCatalogue(rows=[row])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['nature'], keywords=['park']), Preferences(), None, set()
    )

    assert options == []


def test_title_address_and_broad_category_do_not_establish_a_type():
    row = catalogue_place('unknown', 'Park Museum Restaurant Cafe', 'food')
    row['address'] = 'Cafe Road'
    catalogue = LocalCatalogue(rows=[row])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['food'], keywords=['cafe']), Preferences(), None, set()
    )

    assert options == []


def test_restaurant_search_does_not_include_a_hawker_centre():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('hawker', 'Restaurant Food Centre', 'food', 'hawker_centre'),
        catalogue_place('restaurant', 'Dinner', 'food', 'chinese_restaurant'),
    ])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['food'], keywords=['restaurant']), Preferences(), None, set()
    )

    assert [option['identity'] for option in options] == ['catalogue:restaurant']
    assert options[0]['place_type_basis'] == 'overture.category'


def test_broad_nature_without_a_fine_type_still_includes_beaches():
    catalogue = LocalCatalogue(rows=[catalogue_place('beach', 'Sunny Shore', 'nature', 'beach')])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['nature']), Preferences(), None, set()
    )

    assert [option['identity'] for option in options] == ['catalogue:beach']
    assert options[0]['place_types'] == []


def test_legacy_park_preference_prioritizes_park_evidence_over_other_nature():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('beach', 'A Shore', 'nature', 'beach'),
        catalogue_place('park', 'Z Green', 'nature', 'park'),
    ])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['nature']), Preferences(preferred=['park']), None, set()
    )

    assert [option['identity'] for option in options] == ['catalogue:park', 'catalogue:beach']


def test_multiple_hard_keywords_are_all_required():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('quiet', 'Quiet Room', 'food', 'cafe'),
        catalogue_place('water', 'Waterside Cup', 'food', 'cafe'),
        catalogue_place('both', 'Quiet Waterside Cup', 'food', 'cafe'),
    ])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['food'], keywords=['quiet', 'waterside']), Preferences(), None, set(),
        requirements=[requirement('keyword', 'quiet'), requirement('keyword', 'waterside')]
    )

    assert [option['identity'] for option in options] == ['catalogue:both']


def test_unsupported_hard_type_is_not_treated_as_a_match():
    catalogue = LocalCatalogue(rows=[catalogue_place('unknown', 'Unicorn Park', 'nature', 'park')])
    action = LocationAction(op='add_visit', place=PlaceSpec(categories=['nature']), quote='unicorn sanctuary')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), NoGeocoder(),
                         requirements=[requirement('place_type', 'unicorn sanctuary')])

    assert slot['options'] == []
    assert slot['retrieval_diagnostic']['unsupported_place_types'] == ['unicorn sanctuary']
    assert slot['notices']


@pytest.mark.parametrize(('term', 'expected'), [
    ('公园', 'park'), ('PARKS', 'park'), ('咖啡馆', 'cafe'), ('café', 'cafe'),
    ('coffee shop', 'cafe'), ('餐厅', 'restaurant'), ('博物馆', 'museum'),
    ('theme park', None), ('coffee', None), ('restaurant nearby', None),
])
def test_place_type_aliases_are_bounded(term, expected):
    assert canonical_place_type(term) == expected
    assert SUPPORTED_PLACE_TYPES == {'park', 'cafe', 'restaurant', 'museum'}


def test_named_anchor_radius_filters_all_rows_before_the_shortlist_limit():
    center = catalogue_place('center', 'Bugis MRT', 'nature')
    far = [catalogue_place(f'far-{i}', f'Museum {i}', 'tourism', 'museum') for i in range(12)]
    for row in far:
        row['latitude'] = 1.32
    near = catalogue_place('near', 'Nearby Venue', 'tourism')
    near['latitude'] = 1.301
    catalogue = LocalCatalogue(rows=[center, *far, near])
    action = LocationAction(op='add_visit', place=PlaceSpec(categories=['tourism']), quote='near Bugis MRT')

    slot = retrieve_slot(0, action, catalogue, Preferences(preferred=['museum']), None, set(), NoGeocoder(),
                         requirements=[requirement('spatial_anchor', 'Bugis MRT'),
                                       requirement('radius_m', 500)])

    assert [option['identity'] for option in slot['options']] == ['catalogue:near']
    assert slot['spatial_requirement']['status'] == 'applied'
    assert slot['spatial_requirement']['distance_kind'] == 'straight_line'
    assert 100 < slot['options'][0]['distance_hint_m'] < 120


class StaticGeocoder:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, page):
        self.calls.append((query, page))
        return {'results': self.results}


def test_unique_exact_geocoder_anchor_supplies_the_radius_center():
    catalogue = LocalCatalogue(rows=[catalogue_place('cafe', 'Coffee Room', 'food', 'cafe')])
    geocoder = StaticGeocoder([{'label': 'Bugis MRT', 'latitude': 1.30, 'longitude': 103.85}])
    action = LocationAction(op='add_visit', place=PlaceSpec(keywords=['cafe']), quote='cafe near Bugis MRT')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), geocoder,
                         requirements=[requirement('spatial_anchor', 'Bugis MRT'),
                                       requirement('radius_m', 500)])

    assert [option['identity'] for option in slot['options']] == ['catalogue:cafe']
    assert geocoder.calls == [('Bugis MRT', 1)]
    assert slot['spatial_requirement']['status'] == 'applied'


def test_radius_also_filters_geocoded_place_candidates():
    catalogue = LocalCatalogue(rows=[catalogue_place('center', 'Bugis MRT', 'nature')])
    geocoder = StaticGeocoder([{'label': 'Named Venue', 'latitude': 1.32, 'longitude': 103.85}])
    action = LocationAction(op='add_visit', place=PlaceSpec(query='Named Venue'), quote='Named Venue')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), geocoder,
                         requirements=[requirement('spatial_anchor', 'Bugis MRT'),
                                       requirement('radius_m', 500)])

    assert slot['options'] == []


def test_named_anchor_without_radius_orders_by_distance_without_cutting_off_far_places():
    center = catalogue_place('center', 'Bugis MRT', 'nature')
    near = catalogue_place('near', 'Tea Room', 'food', 'cafe')
    near['latitude'] = 1.301
    far = catalogue_place('far', 'Cafe', 'food', 'cafe')
    far['latitude'] = 1.32
    catalogue = LocalCatalogue(rows=[center, far, near])
    action = LocationAction(op='add_visit', place=PlaceSpec(keywords=['cafe']), quote='near Bugis MRT')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), NoGeocoder(),
                         requirements=[requirement('spatial_anchor', 'Bugis MRT')])

    assert [option['identity'] for option in slot['options']] == ['catalogue:near', 'catalogue:far']
    assert slot['spatial_requirement']['radius_m'] is None


@pytest.mark.parametrize('radius', [0, -500, '500', True])
def test_invalid_radius_is_unresolved_instead_of_ignored(radius):
    catalogue = LocalCatalogue(rows=[catalogue_place('cafe', 'Coffee Room', 'food', 'cafe')])
    action = LocationAction(op='add_visit', place=PlaceSpec(keywords=['cafe']), quote='nearby cafe')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), NoGeocoder(),
                         requirements=[requirement('radius_m', radius)])

    assert slot['options'] == []
    assert slot['spatial_requirement']['status'] == 'unresolved'


def test_ambiguous_catalogue_anchor_does_not_fall_back_to_the_origin():
    first = catalogue_place('center-a', 'Bugis MRT', 'nature')
    second = catalogue_place('center-b', 'Bugis MRT', 'nature')
    second['latitude'] = 1.32
    cafe = catalogue_place('cafe', 'Cup', 'food', 'cafe')
    catalogue = LocalCatalogue(rows=[first, second, cafe])
    origin = SimpleNamespace(latitude=1.30, longitude=103.85, label='Origin')
    action = LocationAction(op='add_visit', place=PlaceSpec(keywords=['cafe']), quote='near Bugis MRT')

    slot = retrieve_slot(0, action, catalogue, Preferences(), origin, set(), NoGeocoder(),
                         requirements=[requirement('spatial_anchor', 'Bugis MRT'),
                                       requirement('radius_m', 500)])

    assert slot['options'] == []
    assert slot['spatial_requirement']['status'] == 'unresolved'
    assert slot['spatial_requirement']['anchor_label'] == 'Bugis MRT'


def test_nonexact_geocoder_anchor_remains_pending_even_with_an_origin():
    catalogue = LocalCatalogue(rows=[catalogue_place('cafe', 'Cup', 'food', 'cafe')])
    geocoder = StaticGeocoder([{'label': 'Bugis Village', 'latitude': 1.30, 'longitude': 103.85}])
    origin = SimpleNamespace(latitude=1.30, longitude=103.85, label='Origin')
    action = LocationAction(op='add_visit', place=PlaceSpec(keywords=['cafe']), quote='near Bugis MRT')

    slot = retrieve_slot(0, action, catalogue, Preferences(), origin, set(), geocoder,
                         requirements=[requirement('spatial_anchor', 'Bugis MRT')])

    assert slot['options'] == []
    assert slot['spatial_requirement']['status'] == 'unresolved'
    assert geocoder.calls == [('Bugis MRT', 1)]


def test_radius_with_origin_and_without_named_anchor_uses_the_origin():
    cafe = catalogue_place('cafe', 'Cup', 'food', 'cafe')
    catalogue = LocalCatalogue(rows=[cafe])
    origin = SimpleNamespace(latitude=1.30, longitude=103.85, label='Origin')
    action = LocationAction(op='add_visit', place=PlaceSpec(keywords=['cafe']), quote='within 500 m')

    slot = retrieve_slot(0, action, catalogue, Preferences(), origin, set(), NoGeocoder(),
                         requirements=[requirement('radius_m', 500)])

    assert [option['identity'] for option in slot['options']] == ['catalogue:cafe']
    assert slot['spatial_requirement']['anchor_source'] == 'origin'
    assert slot['spatial_requirement']['status'] == 'applied'


def test_radius_without_any_center_remains_pending():
    catalogue = LocalCatalogue(rows=[catalogue_place('cafe', 'Cup', 'food', 'cafe')])
    action = LocationAction(op='add_visit', place=PlaceSpec(keywords=['cafe']), quote='within 500 m')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), NoGeocoder(),
                         requirements=[requirement('radius_m', 500)])

    assert slot['options'] == []
    assert slot['spatial_requirement']['status'] == 'unresolved'


def test_anchor_provider_failure_returns_pending_instead_of_inventing_a_center():
    class FailingGeocoder:
        def search(self, *args):
            raise ProviderError('NETWORK_TIMEOUT', 'Unavailable')

    catalogue = LocalCatalogue(rows=[catalogue_place('cafe', 'Cup', 'food', 'cafe')])
    action = LocationAction(op='add_visit', place=PlaceSpec(keywords=['cafe']), quote='near Bugis MRT')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), FailingGeocoder(),
                         requirements=[requirement('spatial_anchor', 'Bugis MRT')])

    assert slot['options'] == []
    assert slot['spatial_requirement']['status'] == 'unresolved'
    assert 'NETWORK_TIMEOUT' in slot['spatial_requirement']['message']


def test_conflicting_radii_remain_pending():
    catalogue = LocalCatalogue(rows=[catalogue_place('cafe', 'Cup', 'food', 'cafe')])
    action = LocationAction(op='add_visit', place=PlaceSpec(keywords=['cafe']), quote='within 500 m and 1 km')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), NoGeocoder(),
                         requirements=[requirement('radius_m', 500), requirement('radius_m', 1000)])

    assert slot['options'] == []
    assert slot['spatial_requirement']['status'] == 'conflicting'


def test_geocoded_places_cannot_bypass_multiple_hard_keywords():
    geocoder = StaticGeocoder([{'label': 'Named Venue Quiet', 'latitude': 1.30, 'longitude': 103.85}])
    action = LocationAction(op='add_visit',
                            place=PlaceSpec(query='Named Venue', keywords=['quiet', 'waterside']),
                            quote='Named Venue quiet waterside')

    slot = retrieve_slot(0, action, LocalCatalogue(rows=[]), Preferences(), None, set(), geocoder,
                         requirements=[requirement('keyword', 'quiet'), requirement('keyword', 'waterside')])

    assert slot['options'] == []


def test_generic_type_query_preserves_six_existing_keywords_without_overflow():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('cafe', 'Quiet Cup', 'food', 'cafe'),
        catalogue_place('hawker', 'Quiet Cafe Food Centre', 'food', 'hawker_centre'),
    ])
    words = ['quiet', 'green', 'waterside', 'local', 'art', 'views']
    action = LocationAction(op='add_visit', place=PlaceSpec(query='cafe', keywords=words), quote='cafe')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), NoGeocoder())

    assert [option['identity'] for option in slot['options']] == ['catalogue:cafe']
    assert slot['retrieval_diagnostic']['specific_keyword_count'] == 6
    assert action.place.keywords == words


def test_hard_category_filters_the_broad_or_recall_categories():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('beach', 'A Beach', 'nature', 'beach'),
        catalogue_place('food', 'Dinner', 'food', 'restaurant'),
    ])
    action = LocationAction(op='add_visit', place=PlaceSpec(categories=['food', 'nature']), quote='food')

    slot = retrieve_slot(0, action, catalogue, Preferences(), None, set(), NoGeocoder(),
                         requirements=[requirement('category', 'food')])

    assert [option['identity'] for option in slot['options']] == ['catalogue:food']


def test_multiple_hard_categories_must_all_have_catalogue_evidence():
    both = catalogue_place('both', 'Mixed Venue', 'food')
    both['categories'] = ['food', 'nature']
    catalogue = LocalCatalogue(rows=[
        catalogue_place('nature', 'Green', 'nature'),
        catalogue_place('food', 'Dinner', 'food'), both,
    ])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['food', 'nature']), Preferences(), None, set(),
        requirements=[requirement('category', 'food'), requirement('category', 'nature')]
    )

    assert [option['identity'] for option in options] == ['catalogue:both']


def test_soft_category_prioritizes_without_filtering_other_recall_categories():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('nature', 'A Beach', 'nature'),
        catalogue_place('food', 'Z Dinner', 'food'),
    ])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['food', 'nature']), Preferences(), None, set(),
        requirements=[requirement('category', 'food', hardness='soft')]
    )

    assert [option['identity'] for option in options] == ['catalogue:food', 'catalogue:nature']


def test_broad_categories_keep_or_semantics_without_a_hard_requirement():
    catalogue = LocalCatalogue(rows=[
        catalogue_place('nature', 'A Beach', 'nature'),
        catalogue_place('food', 'Z Dinner', 'food'),
    ])

    options, _, _ = catalogue.search(
        PlaceSpec(categories=['food', 'nature']), Preferences(), None, set()
    )

    assert {option['identity'] for option in options} == {'catalogue:food', 'catalogue:nature'}


def test_a_geocoder_name_cannot_prove_a_hard_category():
    action = LocationAction(op='add_visit', place=PlaceSpec(query='Named Venue'), quote='Named Venue')

    slot = retrieve_slot(0, action, LocalCatalogue(rows=[]), Preferences(), None, set(), NoGeocoder(),
                         requirements=[requirement('category', 'food')])

    assert slot['options'] == []
    assert slot['retrieval_diagnostic']['geocoder_attempted'] is False
