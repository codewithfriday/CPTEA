"""NavTrace (PostgreSQL + MobilityDB) as a track source, and the per-pair parameter adaptation."""
import math
import os

import pytest

from app.tools.add_city_pairs import ARP, BEST_PAIRS, adapt_params


@pytest.mark.parametrize("pair", BEST_PAIRS + ["VTBS-WADD"])
def test_cut_lines_outside_clip_radius(pair):
    from app.defaults import DEFAULT_VALUES, validate
    A, B = pair.split("-")
    a, b = ARP[A], ARP[B]
    v, _ = adapt_params(DEFAULT_VALUES, a, b)
    validate(v, True, DEFAULT_VALUES)
    clip = float(v["CLU-01"])
    for c in v["CLU-02"]:
        for ap_ in (a, b):
            if v["CLU-02b"] == "latitude":
                d = abs(c - ap_[0]) * 60
            else:
                d = abs(c - ap_[1]) * 60 * math.cos(math.radians(ap_[0]))
            assert d > clip, (pair, c, d)
    assert 3 <= len(v["CLU-02"]) <= 7
    assert v["GEN-03"] < DEFAULT_VALUES["GEN-03"] or pair == "VTBS-WADD"
    assert all(2 * r + 50 <= 2000 for r in v["HOR-01b"])


def test_orientation_follows_the_route():
    from app.defaults import DEFAULT_VALUES
    assert adapt_params(DEFAULT_VALUES, ARP["WADD"], ARP["WIII"])[0]["CLU-02b"] == "longitude"
    assert adapt_params(DEFAULT_VALUES, ARP["WIII"], ARP["WSSS"])[0]["CLU-02b"] == "latitude"
    assert adapt_params(DEFAULT_VALUES, ARP["WARR"], ARP["WIII"])[0]["HOR-01b"] == [40, 100]


DSN = os.environ.get("CPTEA_TEST_NAVTRACE_DSN")


@pytest.mark.skipif(not DSN, reason="CPTEA_TEST_NAVTRACE_DSN not set (a MobilityDB database with navtrace.flight_trajectory)")
def test_navtrace_tracks(monkeypatch):
    """Positions, ground altitude and identity from MobilityDB rows (the CTV941 WIDD-WIII sample row)."""
    from app import config
    from app.engine import ingest
    monkeypatch.setattr(config, "NAVTRACE_DSN", DSN)
    st = ingest.navtrace_status("WIII", "WIDD")
    assert st["ok"] and st["flights"] >= 1 and "WIDD-WIII" in st["by_direction"]
    with ingest.navtrace_connect() as con:
        rows = [r for r in ingest.navtrace_flights(con, "WIDD", "WIII") if r["callsign"] == "CTV941"]
        name, tr = next(ingest.navtrace_tracks(con, rows))
    assert name == "CTV941-2026-09-16"
    assert len(tr) > 1000 and tr.t.is_monotonic_increasing and tr.alt.notna().all()
    assert abs(tr.lat.iloc[0] - 1.128566) < 1e-6 and abs(tr.lon.iloc[0] - 104.125484) < 1e-6
    assert tr.alt.max() == 35000 and tr.gs.iloc[0] == pytest.approx(172.7)
    assert (tr.alt == 0).any()  # ground positions after landing get 0 ft
