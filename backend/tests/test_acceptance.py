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
    # plain-language explained report: one PDF, every section present, no heading left at the bottom of a page
    ex = c.post(f"/api/runs/{run['id']}/reports", headers=h, json={"kind": "explained"}).json()
    assert ex["kind"] == "explained" and ex["title"].startswith("Understanding the VTBS")
    t0 = time.time()
    while time.time() - t0 < 300:
        x = [y for y in c.get(f"/api/runs/{run['id']}/reports", headers=h).json() if y["id"] == ex["id"]][0]
        if x["generation"] != "generating":
            break
        time.sleep(2)
    assert x["generation"] == "done", x["generation"] and x["formats"] == ["pdf"]
    pdf_ex = c.get(f"/api/reports/{ex['id']}/download/pdf", headers=h)
    assert pdf_ex.status_code == 200
    pypdf = pytest.importorskip("pypdf")
    import io
    import re

    def pages_text(content):
        return [(pg.extract_text() or "") for pg in pypdf.PdfReader(io.BytesIO(content)).pages]

    ex_pages = pages_text(pdf_ex.content)
    full = "\n".join(ex_pages)
    for sec in ("1. About this report", "2. The main findings", "5. Flying further or shorter than planned", "6. The arrival phase",
                "7. Cruising at the right level", "8. Stopping on the way up and down", "What this shows", "What it tells us"):
        assert sec in full, sec
    assert "10.6 NM" in full  # the run's own numbers are explained

    def last_lines(pages):
        out = []
        for t in pages[:-1]:
            ls = [re.sub(r"\s+", " ", l).strip() for l in t.splitlines()]
            ls = [l for l in ls if l and not re.fullmatch(r"\d+", l) and "· run #" not in l and not l.startswith("CPTEA ·")]
            out.append(ls[-1] if ls else "")
        return out

    from app.reports.explained import GROUP_TITLES
    heads = {"1. About this report", "2. The main findings", "3. The data behind the results", "4. Which routes were used",
             "5. Flying further or shorter than planned", "6. The arrival phase", "7. Cruising at the right level",
             "8. Stopping on the way up and down", "9. All insights and recommendations", "10. Words used in this report",
             "Flights per month", "Where the flights flew", "Route groups", "Filed route against flown distance",
             "Efficiency by airspace (FIR)", "Time in the last part of the flight", "Cruise level score",
             "Climbs stopped below the filed level", "How often flights level off", "Level time by height band",
             "Recommendations"} | {"Executive summary", "Data and coverage", "Method and parameters", "Route classification",
                                   "Horizontal efficiency", "Terminal arrival", "Cruise vertical efficiency",
                                   "Climb and descent level-offs", "Insights and recommendations", "Annex", "Parameter set",
                                   "Excluded flights", "Unresolved flight plan fixes", "Glossary"} | \
        set(GROUP_TITLES.values())
    std_pages = pages_text(c.get(f"/api/reports/{rep['id']}/download/pdf", headers=h).content)
    for name, pages in (("explained", ex_pages), ("standard", std_pages)):
        orphans = [(i + 1, l) for i, l in enumerate(last_lines(pages)) if l in heads]
        assert not orphans, (name, orphans)
    out = Path(os.environ.get("CPTEA_TEST_OUT", tempfile.gettempdir()))
    (out / "acceptance_summary.json").write_text(json.dumps({"extension": S["extension"], "terminal": S["terminal"]["by_ades"],
                                                             "leveloffs": S["leveloffs"]["primary"]}, indent=1))
