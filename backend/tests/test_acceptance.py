"""End-to-end acceptance test against the VTBS-WADD reference results (SRS 10.3).

Needs the VTBS-WADD data: set CPTEA_TEST_DATA to a folder holding Flights/ (FR24 CSV tree) and FPL/ (FPL_VTBS_WADD.json,
fplaug25.parquet.gzip). Skipped otherwise.
"""
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

DATA = os.environ.get("CPTEA_TEST_DATA")
pytestmark = pytest.mark.skipif(not DATA, reason="CPTEA_TEST_DATA not set")


@pytest.fixture(scope="module")
def client():
    tmp = tempfile.mkdtemp()
    os.environ["CPTEA_DATA_DIR"] = tmp
    os.environ["CPTEA_STATIC_DIR"] = str(Path(tmp) / "nostatic")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def wait(c, url, key, done=("done", "ready"), h=None, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(url, headers=h).json()
        if r.get(key) in done:
            return r
        if r.get(key) == "error":
            raise AssertionError(r)
        time.sleep(2)
    raise TimeoutError(url)


def test_vtbs_wadd(client):
    c = client
    tok = c.post("/api/auth/login", json={"username": "admin", "password": "ChangeMe!2026"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    p = c.post("/api/projects", headers=h, json={
        "name": "VTBS–WADD", "adep": "VTBS", "ades": "WADD", "adep_lat": 13.6811, "adep_lon": 100.7475,
        "ades_lat": -8.7482, "ades_lon": 115.1672,
        "callsigns": {"THA431": "VTBS-WADD", "THA439": "VTBS-WADD", "THA432": "WADD-VTBS", "THA440": "WADD-VTBS"}}).json()
    ds = c.post(f"/api/projects/{p['id']}/datasets", headers=h, data={"name": "FR24", "server_path": f"{DATA}/Flights"}).json()
    ds = wait(c, f"/api/datasets/{ds['id']}", "status", ("ready",), h)
    assert ds["n_flights"] == 952
    for f in ("FPL_VTBS_WADD.json", "fplaug25.parquet.gzip"):
        with open(f"{DATA}/FPL/{f}", "rb") as fh:
            assert c.post(f"/api/projects/{p['id']}/fpl/upload", headers=h, files={"file": (f, fh)}).status_code == 200
    ps = c.get(f"/api/projects/{p['id']}/paramsets", headers=h).json()[0]
    run = c.post(f"/api/projects/{p['id']}/runs", headers=h, json={"dataset_id": ds["id"], "paramset_id": ps["id"]}).json()
    run = wait(c, f"/api/runs/{run['id']}", "status", ("done",), h)
    S = c.get(f"/api/runs/{run['id']}/summary", headers=h).json()
    assert S["n"]["flights"] == 951
    assert abs(S["n"]["with_plan"] - 895) <= 5
    assert {c_["cluster"] for c_ in S["clusters"]["summary"]} == {0}
    ext = {(e["ring"], e["dirn"]): e for e in S["extension"]}
    assert abs(ext[(100, "VTBS-WADD")]["median"] - (-10.6)) <= 1
    assert abs(ext[(100, "WADD-VTBS")]["median"] - (-1.4)) <= 1
    assert abs(ext[(40, "VTBS-WADD")]["median"] - 23.0) <= 1
    assert abs(ext[(40, "WADD-VTBS")]["median"] - 8.9) <= 1
    assert abs(ext[(100, "VTBS-WADD")]["time_median"] - 187.3) <= 0.5
    assert abs(ext[(100, "WADD-VTBS")]["time_median"] - 173.9) <= 0.5
    assert abs(ext[(100, "VTBS-WADD")]["time_var"] - 5.4) <= 0.3
    assert abs(ext[(100, "WADD-VTBS")]["time_var"] - 4.1) <= 0.3
    ter = {(t["ades"], t["ring"]): t for t in S["terminal"]["by_ades"]}
    assert abs(100 * ter[("WADD", 100)]["hold_share"] - 13.3) <= 1
    assert abs(100 * ter[("VTBS", 100)]["hold_share"] - 5.1) <= 1
    assert abs(S["terminal"]["corr"]["WADD"] - (-0.05)) <= 0.05
    sens = {(s["phase"], s["airport"], s["dur"]): s["share"] for s in S["leveloffs"]["sensitivity"]}
    assert abs(100 * sens[("descent", "WADD", 20.0)] - 45.2) <= 1
    assert abs(100 * sens[("descent", "WADD", 300.0)] - 8.8) <= 1
    # flight view, tracks, exports, insights, report
    fl = c.get(f"/api/runs/{run['id']}/flights", headers=h).json()["rows"]
    d = c.get(f"/api/runs/{run['id']}/flights/{fl[0]['fid']}", headers=h).json()
    assert d["profile"]["alt"] and d["track"]["lat"]
    assert c.get(f"/api/runs/{run['id']}/tracks", headers=h).json()["tracks"]
    assert c.get(f"/api/runs/{run['id']}/export?fmt=xlsx", headers=h).status_code == 200
    ins = c.get(f"/api/runs/{run['id']}/insights", headers=h).json()
    assert any(i["rule"] == "INS-02" for i in ins)
    for i in ins:
        c.patch(f"/api/insights/{i['id']}", headers=h, json={"status": "accepted"})
    rep = c.post(f"/api/runs/{run['id']}/reports", headers=h, json={"title": "VTBS–WADD test report"}).json()
    t0 = time.time()
    while time.time() - t0 < 300:
        r = [x for x in c.get(f"/api/runs/{run['id']}/reports", headers=h).json() if x["id"] == rep["id"]][0]
        if r["generation"] != "generating":
            break
        time.sleep(2)
    assert r["generation"] == "done", r["generation"]
    for fmt in ("pdf", "docx", "pptx"):
        assert c.get(f"/api/reports/{rep['id']}/download/{fmt}", headers=h).status_code == 200
    out = Path(os.environ.get("CPTEA_TEST_OUT", tempfile.gettempdir()))
    (out / "acceptance_summary.json").write_text(json.dumps({"extension": S["extension"], "terminal": S["terminal"]["by_ades"],
                                                             "leveloffs": S["leveloffs"]["primary"]}, indent=1))
