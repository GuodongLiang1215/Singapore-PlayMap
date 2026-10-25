from app.stage2a.models import Location
from app.stage2b.planning import resolve_point


def test_onemap_search_point_inherits_catalogue_metadata_without_moving_point():
    point = Location(
        latitude=1.286223958762737,
        longitude=103.8592711215884,
        label="MARINA BAY SANDS (ARTSCIENCE MUSEUM)",
        source="onemap_search",
        confirmed=True,
    )

    def matcher(label, lat, lon):
        assert "ARTSCIENCE MUSEUM" in label
        return {"entity_id": "place:stb_attractions:1027", "build_id": "build-1",
                "distance_m": 0.01, "token_overlap": 0.6}

    def resolver(uid, build):
        assert uid == "place:stb_attractions:1027"
        return {"entity": {
            "category": "tourism",
            "suggested_duration": {"short":45,"regular":90,"long":120,"basis":"estimate"},
            "regular_hours_evidence": [],
            "opening_hours_display": "每日 9:00–18:00",
        }}

    result = resolve_point(point, resolver, matcher)

    assert result["point"].source == "onemap_search"
    assert result["point"].latitude == point.latitude
    assert result["point"].longitude == point.longitude
    assert result["category"] == "tourism"
    assert result["suggested_duration"]["regular"] == 90
    assert result["opening_hours_display"] == "每日 9:00–18:00"
    assert result["catalogue_metadata_match"]["entity_id"] == "place:stb_attractions:1027"


def test_unmatched_onemap_search_point_stays_unknown():
    point = Location(
        latitude=1.30, longitude=103.80, label="Some Place",
        source="onemap_search", confirmed=True,
    )
    result = resolve_point(point, lambda *_: (_ for _ in ()).throw(AssertionError()), lambda *_: None)
    assert result["category"] == "unknown"
    assert result["opening_hours_display"] is None
    assert result["source_checked"] is False


def test_verbose_onemap_artscience_label_uses_artscience_catalogue_metadata():
    point = Location(
        latitude=1.286223958762737, longitude=103.8592711215884,
        label="MARINA BAY SANDS (ARTSCIENCE MUSEUM)",
        source="onemap_search", confirmed=True,
    )
    # Regression contract: once the Stage1C matcher selects the reviewed
    # ArtScience entity, planning must not substitute metadata from a nearby POI.
    def matcher(label, lat, lon):
        return {"entity_id":"place:stb_attractions:1027","build_id":"build-1",
                "distance_m":0.01,"name_score":0.95}
    def resolver(uid, build):
        return {"entity":{
            "category":"tourism",
            "suggested_duration":{"short":45,"regular":90,"long":120,"basis":"estimate"},
            "regular_hours_evidence":[],
            "opening_hours_display":"每日 9:00–18:00",
        }}
    result=resolve_point(point,resolver,matcher)
    assert result["category"]=="tourism"
    assert result["opening_hours_display"]=="每日 9:00–18:00"
