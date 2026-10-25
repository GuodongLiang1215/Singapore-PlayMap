from fastapi.testclient import TestClient

from app.stage2c import api as stage2c_api
from app.stage2c_main import app


class FakeCatalogue:
    build_id = "build-1"
    by_id = {
        "park-1": {"entity_id": "park-1", "categories": ["nature"]},
        "food-1": {"entity_id": "food-1", "category": "food"},
    }


def client():
    return TestClient(app, headers={"X-PlayMap-Client": "stage2c"})


def test_visit_category_recovers_catalogue_category(monkeypatch):
    monkeypatch.setattr(stage2c_api, "LocalCatalogue", FakeCatalogue)
    response = client().post("/api/stage2c/visit-category", json={
        "entity_id": "park-1",
        "catalogue_build_id": "build-1",
    })
    assert response.status_code == 200
    assert response.json()["categories"] == ["nature"]


def test_visit_category_rejects_catalogue_change(monkeypatch):
    monkeypatch.setattr(stage2c_api, "LocalCatalogue", FakeCatalogue)
    response = client().post("/api/stage2c/visit-category", json={
        "entity_id": "park-1",
        "catalogue_build_id": "old-build",
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CATALOGUE_CHANGED"


def test_visit_category_rejects_unknown_visit(monkeypatch):
    monkeypatch.setattr(stage2c_api, "LocalCatalogue", FakeCatalogue)
    response = client().post("/api/stage2c/visit-category", json={
        "entity_id": "missing",
        "catalogue_build_id": "build-1",
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VISIT_NOT_IN_CATALOGUE"
