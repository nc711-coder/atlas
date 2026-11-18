"""Tests for the Atlas API.  Run from the project folder:  python -m pytest api -q"""
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["hexes"] > 0


def test_hexes_have_scores_and_factors():
    feats = client.get("/api/hexes").json()["features"]
    p = feats[0]["properties"]
    assert {"score", "rank", "needs_review", "f_underused", "f_parking", "f_transit"} <= set(p)
    assert all(0 <= f["properties"]["score"] <= 100 for f in feats)


def test_unknown_submarket_is_404():
    assert client.get("/api/hexes", params={"submarket": "atlantis"}).status_code == 404


def test_hex_detail_and_missing_hex():
    first = client.get("/api/hexes").json()["features"][0]["properties"]["hex"]
    d = client.get(f"/api/hexes/{first}").json()
    assert d["hex"]["properties"]["hex"] == first and d["parcels"]["type"] == "FeatureCollection"
    assert client.get("/api/hexes/not-a-hex").status_code == 404


def test_rank_default_weights_matches_pipeline_order():
    r = client.post("/api/rank", json={}, params={"top": 5}).json()
    assert [x["rank"] for x in r["top"]] == [1, 2, 3, 4, 5]
    assert abs(sum(r["weights_used"].values()) - 1) < 1e-9
    # with the default weights, the API's #1 is the pipeline's #1
    assert r["top"][0]["default_rank"] == 1


def test_rank_rejects_bad_weights():
    assert client.post("/api/rank", json={"parking": -1}).status_code == 422
    zeros = {k: 0 for k in ["underused", "parking", "transit", "value", "momentum", "land"]}
    assert client.post("/api/rank", json=zeros).status_code == 422


def test_rank_transit_only_puts_nearest_to_metro_first():
    w = {k: 0 for k in ["underused", "parking", "value", "momentum", "land"]} | {"transit": 1}
    top = client.post("/api/rank", json=w, params={"top": 3}).json()["top"]
    assert top[0]["contributions"]["transit"] >= top[-1]["contributions"]["transit"]


def test_meta_has_measured_results():
    m = client.get("/api/meta").json()
    assert m["weights_default"] and "measured" in m
