"""ICAO flight plan parsing, version selection and Item 15 expansion (FR-DATA-07/08/10/11, FR-DATA-20..30)."""
import re
from datetime import datetime, timedelta

SPEED_LEVEL = re.compile(r"^([NMK]\d{3,4})(F\d{3}|A\d{3}|S\d{4}|M\d{4}|VFR)$")
CHANGE = re.compile(r"^([A-Z0-9]{2,11})/([NMK]\d{3,4})(F\d{3}|A\d{3}|S\d{4}|M\d{4})$")
PROC_SUFFIX = re.compile(r"^[A-Z]{2,5}\d[A-Z]$")  # SID/STAR names, e.g. TUMGA1C, NAH3A


def level_ft(code: str):
    if not code:
        return None
    if code[0] == "F":
        return int(code[1:]) * 100
    if code[0] == "A":
        return int(code[1:]) * 100
    if code[0] == "S":  # metric FL in tens of metres
        return round(int(code[1:]) * 10 * 3.28084)
    if code[0] == "M":
        return round(int(code[1:]) * 10 * 3.28084)
    return None


def _clean(msg: str) -> str:
    return re.sub(r"[\x00-\x08\x0b-\x1f]", "", msg.replace("\r", ""))


def parse_message(raw: str) -> dict:
    """Parse a raw ATS message (FPL or CHG). Returns dict with items; 'error' on failure."""
    out = {"msg_type": "", "callsign": "", "type": "", "wake": "", "adep": "", "eobt": "", "speed": "",
           "rfl": None, "route": "", "ades": "", "eet": "", "item18": "", "changes": [], "filed": ""}
    if not raw:
        out["error"] = "empty"
        return out
    m = _clean(raw)
    h = re.search(r"\n(\d{6}) [A-Z]{8}", m)
    if h:
        out["filed"] = h.group(1)
    start = m.find("(")
    if start < 0:
        out["error"] = "no message body"
        return out
    body = m[start + 1:]
    end = body.rfind(")")
    if end >= 0:
        body = body[:end]
    items = [x.replace("\n", " ").strip() for x in re.split(r"\n\s*-", body)]
    head = items[0].split("-")
    out["msg_type"] = head[0][:3]
    out["callsign"] = head[1] if len(head) > 1 else ""
    if out["msg_type"] != "FPL":
        out["items"] = re.split(r"-(?=\d{1,2}/)", body.replace("\n", " "))
        return out
    try:
        out["type"], out["wake"] = (items[1].split("-")[0].split("/") + [""])[:2]
        out["adep"], out["eobt"] = items[2][:4], items[2][4:8]
        tok = items[3].split()
        sl = SPEED_LEVEL.match(tok[0]) if tok else None
        if sl:
            out["speed"] = sl.group(1)
            out["rfl"] = level_ft(sl.group(2))
            route_tok = tok[1:]
        else:
            route_tok = tok
        out["route"] = " ".join(route_tok)
        out["ades"], out["eet"] = items[4][:4], items[4][4:8]
        out["item18"] = " ".join(items[5:])
        for t in route_tok:
            c = CHANGE.match(t)
            if c:
                out["changes"].append({"fix": c.group(1), "speed": c.group(2), "level": level_ft(c.group(3))})
        eets = re.search(r"EET/((?:[A-Z]{4}\d{4}\s*)+)", out["item18"])
        out["fir_eet"] = re.findall(r"([A-Z]{4})(\d{4})", eets.group(1)) if eets else []
    except Exception as e:  # noqa: BLE001
        out["error"] = f"parse error: {e}"
    return out


def parse_api_flight(flight: dict) -> dict:
    """Map structured fields of the AirNav flight data API to the parsed shape (for cross-checking)."""
    lvl = flight.get("level")
    route = (flight.get("route") or "").strip()
    changes = []
    for t in route.split():
        c = CHANGE.match(t)
        if c:
            changes.append({"fix": c.group(1), "speed": c.group(2), "level": level_ft(c.group(3))})
    return {"msg_type": "FPL", "callsign": flight.get("callsign", ""), "type": flight.get("typeOfAircraft", ""),
            "wake": flight.get("wakeTurbulance", ""), "adep": flight.get("adep", ""),
            "eobt": (flight.get("eobt") or "")[11:16].replace(":", ""), "speed": flight.get("speed", ""),
            "rfl": int(lvl) * 100 if lvl and str(lvl).isdigit() else None, "route": route,
            "ades": flight.get("ades", ""), "item18": flight.get("otherInformation", "") or "", "changes": changes}


def cross_check(parsed: dict, api_parsed: dict) -> list:
    flags = []
    for k in ("type", "adep", "ades", "rfl"):
        a, b = parsed.get(k), api_parsed.get(k)
        if a and b and str(a) != str(b):
            flags.append(f"{k}: message {a} vs API {b}")
    ra = " ".join(parsed.get("route", "").split())
    rb = " ".join(api_parsed.get("route", "").split())
    if ra and rb and ra != rb:
        flags.append("route differs between message and API fields")
    return flags


def filed_sort_key(filed: str, dof: str) -> str:
    """Filed DDHHMM to a sortable timestamp, choosing the month so that it is close to the date of flight."""
    if not filed or len(filed) != 6:
        return ""
    try:
        d0 = datetime.strptime(dof, "%Y-%m-%d")
        dd, hh, mm = int(filed[:2]), int(filed[2:4]), int(filed[4:6])
        best = None
        for mo in (-1, 0, 1):
            y, m = d0.year, d0.month + mo
            if m == 0:
                y, m = y - 1, 12
            if m == 13:
                y, m = y + 1, 1
            try:
                cand = datetime(y, m, dd, hh, mm)
            except ValueError:
                continue
            if best is None or abs((cand - d0).total_seconds()) < abs((best - d0).total_seconds()):
                best = cand
        return best.isoformat() if best else ""
    except Exception:  # noqa: BLE001
        return ""


def eobt_dt(dof: str, eobt: str):
    try:
        return datetime.strptime(dof, "%Y-%m-%d") + timedelta(hours=int(eobt[:2]), minutes=int(eobt[2:4]))
    except Exception:  # noqa: BLE001
        return None


def select_version(records: list, rule: str, dof: str):
    """records: list of dicts with keys filed_iso, parsed, msg_type. FR-DATA-08 / FR-DATA-23."""
    fpls = [r for r in records if r["parsed"].get("msg_type") == "FPL" and not r["parsed"].get("error")]
    if not fpls:
        return None
    fpls.sort(key=lambda r: r.get("filed_iso") or "")
    if rule == "first_filed":
        return fpls[0]
    if rule == "last_received":
        return fpls[-1]
    eo = eobt_dt(dof, fpls[-1]["parsed"].get("eobt", ""))
    before = [r for r in fpls if eo is None or not r.get("filed_iso") or r["filed_iso"] <= eo.isoformat()]
    return (before or fpls)[-1]


def route_tokens(route: str) -> list:
    """Item 15 route without speed/level groups."""
    out = []
    for t in route.replace("\n", " ").split():
        t = t.strip()
        if not t:
            continue
        if "-" in t:  # truncated messages occasionally run into item 16
            t = t.split("-")[0]
            if not t:
                break
        out.append(t.split("/")[0])
    return out


def expand_route(route: str, fixes: dict, airways: dict):
    """Expand Item 15 into an ordered list of fix names.

    fixes: name -> (lat, lon); airways: name -> ordered list of fix names.
    Returns (names, blocking_unresolved, edge_unresolved). DCT kept as straight legs; SID/STAR
    designators dropped. Unresolved tokens before the first or after the last resolved fix (for example a
    STAR entry fix missing from the AIS data) are 'edge' warnings: the reference route then joins the last
    resolved fix directly to the aerodrome.
    """
    tok = [t for t in route_tokens(route) if t not in ("DCT",) and not PROC_SUFFIX.match(t)]
    pts, events = [], []  # events: ("ok", name) or ("miss", token)
    i = 0
    while i < len(tok):
        t = tok[i]
        if t in airways:
            if pts and i + 1 < len(tok):
                seq = airways[t]
                s, e = pts[-1], tok[i + 1]
                if s in seq and e in seq:
                    i0, i1 = seq.index(s), seq.index(e)
                    seg = seq[i0 + 1:i1] if i1 > i0 else seq[i1 + 1:i0][::-1]
                    seg = [x for x in seg if x in fixes]
                    pts.extend(seg)
                    events.extend(("ok", x) for x in seg)
                else:
                    events.append(("miss", f"{t}({s}-{e})"))
            i += 1
            continue
        if t in fixes:
            if not pts or pts[-1] != t:
                pts.append(t)
                events.append(("ok", t))
        elif re.match(r"^\d{2,4}[NS]\d{3,5}[EW]$", t) and latlon_token(t):
            fixes[t] = latlon_token(t)
            pts.append(t)
            events.append(("ok", t))
        else:
            events.append(("miss", t))
        i += 1
    ok_idx = [k for k, (kind, _) in enumerate(events) if kind == "ok"]
    blocking, edge = set(), set()
    for k, (kind, name) in enumerate(events):
        if kind != "miss":
            continue
        if not ok_idx or k < ok_idx[0] or k > ok_idx[-1]:
            edge.add(name)
        else:
            blocking.add(name)
    return pts, blocking, edge


def latlon_token(t: str):
    m = re.match(r"^(\d{2})(\d{2})?([NS])(\d{3})(\d{2})?([EW])$", t)
    if not m:
        return None
    lat = int(m.group(1)) + (int(m.group(2)) / 60 if m.group(2) else 0)
    lon = int(m.group(4)) + (int(m.group(5)) / 60 if m.group(5) else 0)
    return (-lat if m.group(3) == "S" else lat, -lon if m.group(6) == "W" else lon)


def planned_profile(names: list, rfl: int, changes: list) -> list:
    """Planned level at each named fix: initial RFL, then each /level change from its fix onward."""
    ch = {c["fix"]: c["level"] for c in changes if c.get("level")}
    lvl = rfl
    out = []
    for n in names:
        if n in ch:
            lvl = ch[n]
        out.append(lvl)
    return out
