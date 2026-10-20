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
    # NavEarth lists some reporting points twice (e.g. LEBEG): a refresh must not fail on them
    "enroute_chart:rep_point": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [98.9, 7.5]},
                                 "properties": {"name_rep": "TUMGA", "source_aip_rep": "AIP"}},
                                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [100.5678, -1.8869]},
                                 "properties": {"name_rep": "LEBEG", "source_aip_rep": "AIRAC AIP AMDT 142 / 21 MAR 24"}},
                                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [100.5678, -1.8869]},
                                 "properties": {"name_rep": "LEBEG", "source_aip_rep": "AIRAC AIP AMDT 142 / 21 MAR 24"}}],
    "enroute_chart:nav_aid_enr": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [115.2, -8.7]},
                                   "properties": {"designator_nav": "BLI"}}],
    "enroute_chart:ats_route_international": [{"type": "Feature", "geometry": None,
                                               "properties": {"name_route": "Q99", "from_name": "AAA", "to_name": "BBB", "segment_sort": 1}}],
    # real NavEarth N875: Indonesian part only, two segments without a sort number
    "enroute_chart:ats_route_rnp_upper": [
        {"type": "Feature", "geometry": None, "properties": {"name_route": "N875", "segment_sort": a, "from_name": b, "to_name": c}}
        for a, b, c in [(None, "OMLIV", "UPLAM"), (None, "UPLAM", "BOBOB"), (7, "AKDAT", "LEBIN"), (8, "LEBIN", "OMLIV"),
                        (9, "BOBOB", "ESPIT"), (10, "ESPIT", "NIMIX"), (11, "NIMIX", "ARUPA"), (12, "ARUPA", "PNK")]],
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
    assert (r["fixes"], r["navaids"], r["airways"], r["tma"], r["duplicates"]) == (2, 1, 2, 1, 1) and not r["errors"]
    # refreshing twice updates fixes and replaces the TMA set instead of duplicating them
    assert client.post("/api/ais/navearth-refresh", headers=admin).status_code == 200
    from app.db import SessionLocal as _S
    from app.models import Fix
    with _S() as db:
        assert db.query(Fix).filter_by(name="LEBEG", source="navearth").count() == 1
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


def test_chain_segments_ignores_missing_sort():
    from app.engine.ingest import chain_segments
    segs = [(None, "OMLIV", "UPLAM"), (None, "UPLAM", "BOBOB"), (7, "AKDAT", "LEBIN"), (8, "LEBIN", "OMLIV"),
            (9, "BOBOB", "ESPIT"), (10, "ESPIT", "NIMIX"), (11, "NIMIX", "ARUPA"), (12, "ARUPA", "PNK")]
    assert chain_segments(segs) == [["AKDAT", "LEBIN", "OMLIV", "UPLAM", "BOBOB", "ESPIT", "NIMIX", "ARUPA", "PNK"]]
    # split airway -> two chains, longest first
    assert chain_segments([(1, "A", "B"), (2, "B", "C"), (3, "X", "Y")]) == [["A", "B", "C"], ["X", "Y"]]


def test_partial_navearth_airway_does_not_hide_full_one():
    """VTBS-WADD plans file ENREP N875 PNK; NavEarth's N875 starts at AKDAT. The full imported N875 must be used."""
    from app.engine.fpl import airway_path, expand_route
    full = ["ENREP", "NOPAT", "DAMOG", "SUSAR", "MUMSO", "ELGOR", "AKDAT", "LEBIN", "OMLIV", "UPLAM", "BOBOB", "ESPIT",
            "NIMIX", "ARUPA", "PNK"]
    navearth = ["AKDAT", "LEBIN", "OMLIV", "UPLAM", "BOBOB", "ESPIT", "NIMIX", "ARUPA", "PNK"]
    fixes = {n: (0.0, float(i)) for i, n in enumerate(full + ["BUT", "KOLTA", "PKN", "SUGIK", "BUKTU"])}
    airways = {"N875": [navearth, full], "G464": [["BLI", "KOLTA", "PKN", "SUGIK", "BUKTU", "PNK"]]}
    names, blocking, edge = expand_route("N0490F390 ENREP N875 PNK G464 KOLTA", fixes, airways)
    assert not blocking and names[:2] == ["ENREP", "NOPAT"] and names[-3:] == ["SUGIK", "PKN", "KOLTA"]
    # reverse direction, and joining two partial sources that only share AKDAT
    assert airway_path([navearth, full], "PNK", "ENREP")[0] == "ARUPA"
    assert airway_path([["ENREP", "NOPAT", "AKDAT"], navearth], "ENREP", "PNK") == ["NOPAT"] + navearth[:-1]
    assert airway_path([navearth], "ENREP", "PNK") is None
