from app.stage2c.catalogue import LocalCatalogue
from app.stage2c.models import PlaceSpec, Preferences


def test_candidate_carries_duration_hours_and_source_evidence():
    catalogue = LocalCatalogue(rows=[{
        'entity_id': 'overture-cafe-1',
        'display_title': 'Evidence Cafe',
        'categories': ['food'],
        'category': 'food',
        'address': 'Bugis',
        'search_text': 'evidence cafe bugis',
        'search_tags': ['cafe'],
        'latitude': 1.3000,
        'longitude': 103.8550,
        'map_eligible': True,
        'default_exploration_visible': True,
        'data_source': 'overture',
        'suggested_duration': {'short': 30, 'regular': 45, 'long': 60, 'basis': 'category_default'},
        'opening_hours_display': 'Mo-Su 10:00-22:00（OSM 参考）',
        'opening_hours_source': 'osm_reference',
        'entrance_points': [{'name': 'main'}],
    }], build_id='evidence-build')

    options, _, _, _ = catalogue.search_detailed(
        PlaceSpec(categories=['food']), Preferences(), None, set()
    )

    assert len(options) == 1
    assert options[0]['suggested_duration']['basis'] == 'category_default'
    assert options[0]['opening_hours_source'] == 'osm_reference'
    assert options[0]['data_source'] == 'overture'
    assert options[0]['entrance_count'] == 1
