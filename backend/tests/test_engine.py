"""Unit tests for parsing and geometry (run: pytest backend/tests)."""
import numpy as np

from app.engine.fpl import expand_route, parse_message, planned_profile, select_version
from app.engine.geo import ellipsoid, gc, ring_window

FPL = ("\x01GCA6434 010842\r\nFF WRRRZPZX\r\n010842 WADDZPZX\r\n\x02(FPL-THA432-IS\r\n-A359/H-SDE2E3GHIJ1J4J5M1RWYZ/LB2D1\r\n"
       "-WADD0855\r\n-N0492F380 BLI KOLTA G464 PNK N875 ENREP/N0488F400 M904 TONIK M644\r\n ALUMO Y12 ALEMI DCT TUMGA\r\n"
       "-VTBS0346 VTBD\r\n-PBN/A1B1C1D1L1O1S2 NAV/RNP2 DOF/250901 REG/HSTHM EET/WIIF0109\r\n WSJC0205 VTBB0238 SEL/EJGH)\r\n")
API_FPL = ("\x01GCB3836 070915\r\nFF WRRRZPZX\r\n070914 WIIIGIAV\r\n\x02(FPL-GIA536-IS\r\n-B738/M-SDE2E3FGHIRWY/LB1\r\n"
           "-WIII1115\r\n-N0454F330 EGUKO T9 BUTNU\r\n-WAOO0136 WAAA\r\n-PBN/A1C1D1O1S1S2 DOF/260507 REG/PKGMI EET/WAAF0043 SEL/DJMR\r\n"
           " CODE/8A020E OPR/GIA ORGN/WIIIGIAX PER/C)\r\n\x0b\x03")


def test_parse_fpl():
    p = parse_message(FPL)
    assert p["msg_type"] == "FPL"
    assert p["callsign"] == "THA432"
    assert p["type"] == "A359"
    assert p["adep"] == "WADD" and p["ades"] == "VTBS"
    assert p["rfl"] == 38000
    assert p["changes"] == [{"fix": "ENREP", "speed": "N0488", "level": 40000}]
    assert ("WIIF", "0109") in p["fir_eet"]
    assert p["filed"] == "010842"


def test_parse_api_message():
    p = parse_message(API_FPL)
    assert p["callsign"] == "GIA536" and p["type"] == "B738" and p["rfl"] == 33000
    assert p["route"] == "EGUKO T9 BUTNU"


def test_expand_and_profile():
    fixes = {n: (i, i) for i, n in enumerate(["BLI", "KOLTA", "PKN", "SUGIK", "BUKTU", "PNK", "ARUPA", "ENREP", "UPRON", "TONIK", "ALUMO", "ALEMI"])}
    airways = {"G464": ["PNK", "BUKTU", "SUGIK", "PKN", "KOLTA", "BLI"], "N875": ["ENREP", "ARUPA", "PNK"],
               "M904": ["TONIK", "UPRON", "ENREP"], "M644": ["TONIK", "ALUMO"], "Y12": ["ALUMO", "ALEMI"]}
    p = parse_message(FPL)
    names, blocking, edge = expand_route(p["route"], dict(fixes), airways)
    assert names[:5] == ["BLI", "KOLTA", "PKN", "SUGIK", "BUKTU"]
    assert names[-3:] == ["TONIK", "ALUMO", "ALEMI"]
    assert not blocking and edge == {"TUMGA"}
    lv = planned_profile(names, p["rfl"], p["changes"])
    assert lv[names.index("ENREP")] == 40000 and lv[0] == 38000


def test_version_selection():
    recs = [{"filed_iso": "2025-09-01T08:00:00", "parsed": {"msg_type": "FPL", "eobt": "0855", "rfl": 38000}},
            {"filed_iso": "2025-09-01T08:40:00", "parsed": {"msg_type": "FPL", "eobt": "0855", "rfl": 40000}},
            {"filed_iso": "2025-09-01T09:30:00", "parsed": {"msg_type": "FPL", "eobt": "0855", "rfl": 36000}}]
    assert select_version(recs, "last_before_eobt", "2025-09-01")["parsed"]["rfl"] == 40000
    assert select_version(recs, "first_filed", "2025-09-01")["parsed"]["rfl"] == 38000
    assert select_version(recs, "last_received", "2025-09-01")["parsed"]["rfl"] == 36000


def test_geodesy():
    d = gc(13.6811, 100.7475, -8.7482, 115.1672)
    assert abs(d - 1597.9) < 1.0  # VTBS-WADD great circle
    e = ellipsoid(13.6811, 100.7475, -8.7482, 115.1672)
    assert abs(e - d) / d < 0.01  # NFR-05 order of magnitude
    la = np.linspace(13.6811, -8.7482, 200)
    lo = np.linspace(100.7475, 115.1672, 200)
    i, j = ring_window(la, lo, (13.6811, 100.7475), (-8.7482, 115.1672), 100)
    assert gc(la[i], lo[i], 13.6811, 100.7475) >= 100 and gc(la[j], lo[j], -8.7482, 115.1672) >= 100
