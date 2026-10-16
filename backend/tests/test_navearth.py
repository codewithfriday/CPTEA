"""NavEarth WFS address as an administrator setting, layer requests and TMA polygon import."""
import os
import tempfile
from pathlib import Path

import httpx
import pytest

TMA = {"type": "Feature", "id": "tma_polygon.128",
       "geometry": {"type": "MultiPolygon", "coordinates": [[[[113.4558, -0.9275], [114.6667, -1.1347], [117.1833, -3.6167],
                                                               [116.262, -5.3772], [113.4558, -0.9275]]]]},
       "properties": {"gid": 128, "layer": "BANJARMASIN TMA", "upper": "FL245", "lower": "10000 ft", "class": "B",
                      "unt_srvc": "Banjarmasin APP", "source_aip": "AIRAC AIP AMDT 120 / 08 SEP 22"}}
LAYERS = {
    "enroute_chart:rep_point": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [98.9, 7.5]},
                                 "properties": {"name_rep": "TUMGA", "source_aip_rep": "AIP"}}],
    "enroute_chart:nav_aid_enr": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [115.2, -8.7]},
                                   "properties": {"designator_nav": "BLI"}}],
    "enroute_chart:ats_route_international": [{"type": "Feature", "geometry": None,
                                               "properties": {"name_route": "Q99", "from_name": "AAA", "to_name": "BBB", "segment_sort": 1}}],
    "enroute_chart:ats_route_rnp_upper": [],
    "enroute_chart:tma_polygon": [TMA],
}


class FakeResponse:
    def __init__(self, url, params):
        self.url, self.params = url, params
        name = params.get("typeName") or params.get("typeNames")
        self.feats = LAYERS.get(name)
        self.status_code = 200 if self.feats is not None else 400
        self.text = "" if self.feats is not None else "<ServiceExceptionReport>Unknown type</ServiceExceptionReport>"

    def raise_for_status(self):
        pass

    def json(self):
        if self.feats is None:
            raise ValueError("not json")
        return {"type": "FeatureCollection", "features": self.feats}


@pytest.fixture(scope="module")
def client():
    if "CPTEA_DATA_DIR" not in os.environ:
        tmp = tempfile.mkdtemp()
        os.environ["CPTEA_DATA_DIR"] = tmp
        os.environ["CPTEA_STATIC_DIR"] = str(Path(tmp) / "nostatic")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client):
    from app import config
    r = client.post("/api/auth/login", json={"username": config.ADMIN_USERNAME, "password": config.ADMIN_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_wfs_request_from_full_link():
    from app.engine.ingest import wfs_request
    link = ("https://app-pia.airnavindonesia.co.id/geoserver/enroute_chart/ows?service=WFS&version=1.0.0&request=GetFeature"
            "&typeName=enroute_chart:tma_polygon&outputFormat=application/json")
    base, p = wfs_request(link, "enroute_chart:rep_point", 1)
    assert base == "https://app-pia.airnavindonesia.co.id/geoserver/enroute_chart/ows"
    assert p["typeName"] == "enroute_chart:rep_point" and p["maxFeatures"] == 1 and p["outputFormat"] == "application/json"
    base, p = wfs_request("https://h/ows?VERSION=2.0.0", "a:b", 1)
    assert p["typeNames"] == "a:b" and p["count"] == 1 and "outputFormat" in p


def test_settings_and_refresh(client, admin, monkeypatch):
    from app import config
    from app.engine import ingest
    monkeypatch.setattr(ingest.httpx, "get", lambda url, params=None, timeout=None: FakeResponse(url, params))
    s = client.get("/api/settings/navearth", headers=admin).json()
    assert s["url"] == config.NAVEARTH_WFS and "tma_polygon" in s["url"] and not s["saved"]
    assert client.put("/api/settings/navearth", headers=admin, json={"url": "ftp://x"}).status_code == 400
    new = "https://navearth.example/geoserver/enroute_chart/ows"
    s = client.put("/api/settings/navearth", headers=admin, json={"url": new, "layers": {"tma": "enroute_chart:tma_polygon"}}).json()
    assert s["url"] == new and s["saved"] and s["updated_by"] == config.ADMIN_USERNAME
    t = client.post("/api/ais/navearth-test", headers=admin, json={"url": new, "layers": {"routes": "enroute_chart:nope"}}).json()["results"]
    assert any(r["ok"] and "layer" in r["properties"] for r in t if r["group"] == "tma")
    assert any(not r["ok"] for r in t if r["group"] == "routes")
    r = client.post("/api/ais/navearth-refresh", headers=admin).json()
    assert (r["fixes"], r["navaids"], r["airways"], r["tma"]) == (1, 1, 1, 1) and not r["errors"]
    # refreshing twice replaces the TMA set instead of duplicating it
    client.post("/api/ais/navearth-refresh", headers=admin)
    g = client.get("/api/ais/geojson", headers=admin).json()
    tma = [f for f in g["features"] if f["properties"]["layer"] == "tma"]
    assert len(tma) == 1 and tma[0]["properties"]["name"] == "BANJARMASIN TMA" and tma[0]["properties"]["class"] == "B"
    # TMA polygons never feed the FIR metric
    from app.db import SessionLocal
    from app.models import Airspace
    with SessionLocal() as db:
        assert db.query(Airspace).filter(Airspace.kind != "tma").count() == 0
    s = client.put("/api/settings/navearth", headers=admin, json={"reset": True}).json()
    assert s["url"] == config.NAVEARTH_WFS and not s["saved"]


def test_only_admin_changes_address(client, admin):
    r = client.post("/api/users", headers=admin, json={"username": "ana_wfs", "password": "Analyst!2026x", "role": "analyst"})
    assert r.status_code in (200, 409)
    tok = client.post("/api/auth/login", json={"username": "ana_wfs", "password": "Analyst!2026x"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/settings/navearth", headers=h).status_code == 200
    assert client.put("/api/settings/navearth", headers=h, json={"url": "https://x/ows"}).status_code == 403
