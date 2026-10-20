"""Data loading services: tracks, flight plans (file and API), AIS and airspace (SRS section 4)."""
import csv
import io
import re
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from .. import config
from ..db import SessionLocal
from ..models import Airspace, Airway, Dataset, Fix, Flight, FlightPlanRecord, Project, now
from .fpl import cross_check, filed_sort_key, parse_api_flight, parse_message
from .tracks import clean, content_hash, flight_id, quality_summary, read_track, summarise_flight

SEED = Path(__file__).resolve().parent.parent / "seed"


# ------------------------------------------------------------------ tracks
def collect_csv(sources: list[Path]) -> list[tuple[str, bytes]]:
    """Expand folders and ZIP files into (name, bytes) pairs of CSV files."""
    out = []
    for s in sources:
        s = Path(s)
        if s.is_dir():
            for f in sorted(s.rglob("*.csv")):
                if not f.name.startswith("."):
                    out.append((str(f.relative_to(s)), f.read_bytes()))
        elif s.suffix.lower() == ".zip":
            with zipfile.ZipFile(s) as z:
                for n in sorted(z.namelist()):
                    if n.lower().endswith(".csv") and "/." not in n and not n.split("/")[-1].startswith("."):
                        out.append((n, z.read(n)))
        elif s.suffix.lower() == ".csv":
            out.append((s.name, s.read_bytes()))
    return out


def load_tracks(dataset_id: int, sources: list, params: dict, column_map: dict | None = None):
    """Background job: parse, clean and store all tracks of a dataset (FR-DATA-01..06, 13, 14)."""
    db = SessionLocal()
    ds = db.get(Dataset, dataset_id)
    project = db.get(Project, ds.project_id)
    log = []
    try:
        files = collect_csv(sources)
        ds.n_files = len(files)
        ds.message = f"Parsing {len(files)} files"
        db.commit()
        frames, flights = [], {}
        for k, (name, data) in enumerate(files):
            try:
                tr = read_track(data, name)
                if column_map:
                    pass
                if len(tr) < 10:
                    log.append({"file": name, "result": "skipped", "detail": f"only {len(tr)} points"})
                    continue
                tr, removed = clean(tr)
                fid, cs, dof = flight_id(name, tr)
                if fid in flights:
                    log.append({"file": name, "result": "skipped", "detail": f"duplicate flight {fid}"})
                    continue
                summ = summarise_flight(tr, project, params)
                flights[fid] = dict(fid=fid, callsign=cs, dof=dof, **summ)
                if removed:
                    flights[fid]["flags"] = flights[fid]["flags"] + [f"cleaned_{removed}_points"]
                tr = tr.assign(fid=fid)
                frames.append(tr[["fid", "t", "lat", "lon", "alt", "gs", "trk"]])
                log.append({"file": name, "result": "loaded", "detail": f"{fid}, {len(tr)} points"})
            except Exception as e:  # noqa: BLE001
                log.append({"file": name, "result": "error", "detail": str(e)[:200]})
            if k % 50 == 0:
                ds.message = f"Parsed {k + 1} of {len(files)} files"
                db.commit()
        if not frames:
            raise ValueError("No valid track files found")
        pts = pd.concat(frames, ignore_index=True)
        pts.to_parquet(config.dataset_dir(ds.id) / "points.parquet", index=False)
        for f in flights.values():
            inc = f["gate_to_gate"] and f["duration_min"] >= float(params["GEN-03"])
            db.add(Flight(dataset_id=ds.id, included=inc,
                          exclude_reason="" if inc else ", ".join(x for x in f["flags"] if not x.startswith("cleaned")),
                          **f))
        db.commit()
        fl = pd.DataFrame(flights.values())
        ds.quality = quality_summary(pts, fl, params)
        ds.content_hash = content_hash([d for _, d in files])
        ds.n_flights = len(flights)
        ds.load_log = log
        ds.status, ds.message = "ready", f"{len(flights)} flights loaded from {len(files)} files"
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        ds = db.get(Dataset, dataset_id)
        ds.status, ds.message, ds.load_log = "error", str(e), log
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ flight plans: files
def _record(project_id, cs, dof, raw, source, ref="", extra=None):
    parsed = parse_message(raw)
    if not cs:
        cs = parsed.get("callsign", "")
    return FlightPlanRecord(project_id=project_id, callsign=cs.strip().upper(), dof=dof, source=source, source_ref=ref,
                            filed_time=filed_sort_key(parsed.get("filed", ""), dof), msg_type=parsed.get("msg_type") or "FPL",
                            raw=raw, parsed=parsed, **(extra or {}))


def import_fpl_file(db, project_id: int, path: Path) -> dict:
    """FR-DATA-07: JSON array or Parquet with id, callsign, route, dof, typeOfAircraft, fpl."""
    if path.suffix.lower() in (".parquet", ".gzip", ".pq") or path.name.endswith(".parquet.gzip"):
        df = pd.read_parquet(path)
        rows = df.to_dict("records")
    else:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        rows = data if isinstance(data, list) else data.get("flights") or data.get("data") or []
    existing = {(r.callsign, r.dof, hash(r.raw)) for r in
                db.query(FlightPlanRecord).filter_by(project_id=project_id, source="file").all()}
    added = skipped = errors = 0
    for r in rows:
        raw = r.get("fpl") or r.get("content") or ""
        dof = str(r.get("dof") or "")[:10]
        if len(dof) != 10 or not dof[:4].isdigit():
            m = re.search(r"DOF/(\d{2})(\d{2})(\d{2})", raw or "")
            dof = f"20{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
        cs = str(r.get("callsign") or "")
        if not raw or len(dof) != 10:
            errors += 1
            continue
        key = (cs.upper(), dof, hash(raw.replace("\r", "")))
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        db.add(_record(project_id, cs, dof, raw.replace("\r", ""), "file", str(r.get("id", ""))))
        added += 1
    db.commit()
    return {"added": added, "duplicates": skipped, "errors": errors, "rows": len(rows)}


# ------------------------------------------------------------------ flight plans: API (FR-DATA-20..30)
def api_reachable(base: str, timeout=5) -> tuple[bool, str]:
    try:
        r = httpx.get(f"{base.rstrip('/')}/api/flights", params={"callsign": "TEST0", "dof": "2000-01-01"},
                      timeout=timeout, headers=_auth())
        return True, f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"AirNav flight data API not reachable: check the VPN connection ({e.__class__.__name__})"


def _auth():
    return {"Authorization": f"Bearer {config.FLIGHT_API_TOKEN}"} if config.FLIGHT_API_TOKEN else {}


def fetch_one(base, callsign, dof, timeout, retries):
    last = None
    for _ in range(int(retries) + 1):
        try:
            r = httpx.get(f"{base.rstrip('/')}/api/flights", params={"callsign": callsign, "dof": dof}, timeout=timeout,
                          headers=_auth())
            r.raise_for_status()
            return r.json(), None
        except Exception as e:  # noqa: BLE001
            last = str(e)
    return None, last


def api_records(project_id, callsign, dof, payload, takeoff_ts, project, window_h):
    """Turn one API response into FlightPlanRecords (FR-DATA-22..25)."""
    recs, notes = [], []
    flights = (payload or {}).get("flights") or []
    if not flights:
        return [], ["not found"]
    cands = []
    for fl in flights:
        f = fl.get("flight", {})
        ok_dir = {f.get("adep"), f.get("ades")} <= {project.adep, project.ades}
        eobt = f.get("eobt")
        dt = None
        if eobt and takeoff_ts:
            try:
                e = datetime.fromisoformat(eobt.replace("Z", "+00:00")).timestamp()
                dt = abs(e - takeoff_ts) / 3600
            except ValueError:
                pass
        cands.append((0 if ok_dir else 1, dt if dt is not None else 99, fl))
    cands.sort(key=lambda x: (x[0], x[1]))
    best = cands[0]
    if best[0] == 1:
        return [], ["no flight with matching aerodromes"]
    if best[1] != 99 and best[1] > window_h:
        notes.append(f"EOBT {best[1]:.1f} h from take-off (outside window)")
    if len(cands) > 1:
        notes.append(f"{len(cands)} flights returned; nearest EOBT kept")
    fl = best[2]
    f = fl.get("flight", {})
    api_parsed = parse_api_flight(f)
    for m in fl.get("messages") or []:
        raw = (m.get("content") or "").replace("\r", "")
        rec = _record(project_id, callsign, dof, raw, "api", f.get("id", ""),
                      {"atd": f.get("atd") or "", "ata": f.get("ata") or "", "status": f.get("status") or ""})
        if m.get("createdAt"):
            rec.filed_time = m["createdAt"][:19]
        if rec.parsed.get("msg_type") == "FPL":
            flags = cross_check(rec.parsed, api_parsed)
            rec.parsed = {**rec.parsed, "api_flags": flags, "api_notes": notes, "api_fields": api_parsed}
        recs.append(rec)
    if not any(r.parsed.get("msg_type") == "FPL" for r in recs):
        # no raw FPL in history: build one from the structured fields
        rec = FlightPlanRecord(project_id=project_id, callsign=callsign, dof=dof, source="api", source_ref=f.get("id", ""),
                               filed_time=(f.get("createdAt") or "")[:19], msg_type="FPL", raw="",
                               parsed={**api_parsed, "from_fields": True, "api_notes": notes},
                               atd=f.get("atd") or "", ata=f.get("ata") or "", status=f.get("status") or "")
        recs.append(rec)
    return recs, notes


def fetch_fpl_api(project_id: int, dataset_id: int, params: dict, force=False, only_fid: str | None = None) -> dict:
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        base = params["FPL-04"]
        ok, msg = api_reachable(base)
        if not ok:
            return {"ok": False, "message": msg}
        q = db.query(Flight).filter(Flight.dataset_id == dataset_id)
        if only_fid:
            q = q.filter(Flight.fid == only_fid)
        flights = q.all()
        cutoff = now() - timedelta(days=float(params["FPL-07"]))
        todo = []
        for f in flights:
            have = db.query(FlightPlanRecord).filter_by(project_id=project_id, callsign=f.callsign, dof=f.dof, source="api")
            if not force and have.filter(FlightPlanRecord.fetched_at >= cutoff).count():
                continue
            todo.append(f)
        timeout, retries = (list(params["FPL-05"]) + [10, 3])[:2]
        stats = {"fetched": 0, "not_found": 0, "errors": 0, "cached": len(flights) - len(todo), "by_callsign": {}}

        def work(f):
            return f, fetch_one(base, f.callsign, f.dof, float(timeout), int(retries))

        with ThreadPoolExecutor(max_workers=int(params["FPL-06"])) as ex:
            for f, (payload, err) in ex.map(work, todo):
                s = stats["by_callsign"].setdefault(f.callsign, {"fetched": 0, "not_found": 0, "errors": 0})
                if err:
                    stats["errors"] += 1
                    s["errors"] += 1
                    continue
                recs, notes = api_records(project_id, f.callsign, f.dof, payload, f.t_first, project, float(params["FPL-08"]))
                db.query(FlightPlanRecord).filter_by(project_id=project_id, callsign=f.callsign, dof=f.dof, source="api").delete()
                if not recs:
                    stats["not_found"] += 1
                    s["not_found"] += 1
                    continue
                for r in recs:
                    db.add(r)
                stats["fetched"] += 1
                s["fetched"] += 1
                db.commit()
        return {"ok": True, "message": msg, **stats}
    finally:
        db.close()


# ------------------------------------------------------------------ AIS
def upsert_fix(db, name, lat, lon, source, kind="waypoint", airac=""):
    """Insert or update a fix. Sources can list the same name twice (NavEarth does), and the session does not
    autoflush, so fixes added earlier in this session are remembered here instead of being inserted again.
    Returns True when the name was already seen in this session (a duplicate)."""
    cache = db.info.setdefault("fix_cache", {})
    key = (name, source)
    f = cache.get(key)
    dup = f is not None
    if f is None:
        f = db.query(Fix).filter_by(name=name, source=source).first()
    if f:
        f.lat, f.lon, f.kind, f.airac = lat, lon, kind, airac
    else:
        f = Fix(name=name, lat=lat, lon=lon, source=source, kind=kind, airac=airac)
        db.add(f)
    cache[key] = f
    return dup


def import_fixes_csv(db, data: bytes, source="import") -> int:
    rd = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    n = 0
    for r in rd:
        name = (r.get("name") or r.get("fix") or "").strip().upper()
        if not name:
            continue
        upsert_fix(db, name, float(r["lat"]), float(r["lon"]), (r.get("source") or source).strip() or source,
                   (r.get("kind") or "waypoint").strip(), (r.get("airac") or "").strip())
        n += 1
    db.commit()
    return n


def import_airways_csv(db, data: bytes, source="import") -> int:
    rd = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    seqs = {}
    for r in rd:
        seqs.setdefault((r["airway"].strip().upper(), (r.get("source") or source).strip() or source), []).append(
            (float(r.get("seq") or 0), r["fix"].strip().upper()))
    for (a, src), lst in seqs.items():
        fx = [x for _, x in sorted(lst)]
        w = db.query(Airway).filter_by(name=a, source=src).first()
        if w:
            w.fixes = fx
        else:
            db.add(Airway(name=a, fixes=fx, source=src))
    db.commit()
    return len(seqs)


def seed_navdata(db):
    if db.query(Fix).count() == 0:
        import_fixes_csv(db, (SEED / "fixes_seed.csv").read_bytes())
        import_airways_csv(db, (SEED / "airways_seed.csv").read_bytes())


def chain_segments(segs: list) -> list[list[str]]:
    """Join airway segments (sort, from, to) into ordered fix chains by following shared fixes.
    The sort number only breaks ties: NavEarth leaves it empty or repeats it on some segments. A route that is
    split (e.g. an airway that leaves and re-enters the FIR) gives several chains, longest first."""
    adj, order = {}, {}
    for k, (srt, a, b) in enumerate(segs):
        if not a or not b or a == b:
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
        order.setdefault(a, (srt if srt is not None else 1e9, k))
        order.setdefault(b, (srt if srt is not None else 1e9, k))
    seen, chains = set(), []
    # start from chain ends (one neighbour), lowest sort first, then from whatever is left (loops)
    starts = sorted([n for n in adj if len(adj[n]) == 1], key=lambda n: order[n]) + sorted(adj, key=lambda n: order[n])
    for st in starts:
        if st in seen:
            continue
        chain, cur = [st], st
        seen.add(st)
        while True:
            nxt = sorted((x for x in adj[cur] if x not in seen), key=lambda n: order[n])
            if not nxt:
                break
            cur = nxt[0]
            chain.append(cur)
            seen.add(cur)
        if len(chain) > 1:
            chains.append(chain)
    chains.sort(key=len, reverse=True)
    return chains


def flatten_chains(chains: list[list[str]]) -> list:
    """Store several chains in one airway row, separated by None."""
    out = []
    for c in chains:
        if out:
            out.append(None)
        out.extend(c)
    return out


def split_chains(fixes: list) -> list[list[str]]:
    out, cur = [], []
    for f in fixes or []:
        if f is None:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(f)
    if cur:
        out.append(cur)
    return out


# ------------------------------------------------------------ NavEarth WFS (FR-DATA-12)
NAVEARTH_KEY = "navearth_wfs"
DEFAULT_LAYERS = {
    "points": "enroute_chart:rep_point",
    "navaids": "enroute_chart:nav_aid_enr",
    "routes": "enroute_chart:ats_route_international, enroute_chart:ats_route_rnp_upper",
    "tma": "enroute_chart:tma_polygon",
}
LAYER_LABELS = {"points": "Reporting points", "navaids": "En-route navaids", "routes": "ATS routes", "tma": "TMA polygons"}


def navearth_defaults() -> dict:
    return {"url": config.NAVEARTH_WFS, "layers": dict(DEFAULT_LAYERS)}


def navearth_settings(db) -> dict:
    """The saved WFS address and layer names, falling back to the defaults (CPTEA_NAVEARTH_WFS)."""
    from ..models import AppSetting
    row = db.get(AppSetting, NAVEARTH_KEY)
    d = navearth_defaults()
    if row and row.value:
        d["url"] = row.value.get("url") or d["url"]
        d["layers"] = {**d["layers"], **{k: v for k, v in (row.value.get("layers") or {}).items() if k in DEFAULT_LAYERS}}
        d.update(saved=True, updated_by=row.updated_by, updated_at=row.updated_at)
    else:
        d.update(saved=False, updated_by="", updated_at=None)
    return d


def check_wfs_url(url: str) -> str:
    from urllib.parse import urlsplit
    url = (url or "").strip()
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("The WFS address must start with http:// or https:// and include a host")
    return url


def wfs_request(url: str, type_name: str, limit: int | None = None) -> tuple[str, dict]:
    """Endpoint and query for one layer. The address may be a bare endpoint (…/ows) or a full GetFeature link:
    its service, version and output format are kept, and typeName is replaced by the layer asked for."""
    from urllib.parse import parse_qsl, urlsplit, urlunsplit
    u = urlsplit(url.strip())
    base = urlunsplit((u.scheme, u.netloc, u.path, "", ""))
    q = {k: v for k, v in parse_qsl(u.query, keep_blank_values=True)}
    low = {k.lower(): k for k in q}
    params = {k: v for k, v in q.items() if k.lower() not in ("typename", "typenames", "maxfeatures", "count", "request", "service")}
    params["service"] = "WFS"
    params["request"] = "GetFeature"
    version = q.get(low.get("version", ""), "1.0.0") or "1.0.0"
    params[low.get("version", "version")] = version
    if "outputformat" not in low:
        params["outputFormat"] = "application/json"
    v2 = version.startswith("2")
    params["typeNames" if v2 else "typeName"] = type_name
    if limit:
        params["count" if v2 else "maxFeatures"] = limit
    return base, params


def layer_names(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,\s]+", value or "") if x.strip()]


def _fetch_layer(url: str, type_name: str, limit: int | None = None, timeout=60) -> list:
    base, params = wfs_request(url, type_name, limit)
    r = httpx.get(base, params=params, timeout=timeout)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        # GeoServer answers errors as XML with HTTP 200
        txt = re.sub(r"<[^>]+>", " ", r.text)
        raise ValueError("The server did not return GeoJSON: " + " ".join(txt.split())[:200]) from None
    return data.get("features", [])


def _prop(pr: dict, *keys, default=""):
    low = {k.lower(): v for k, v in pr.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, ""):
            return str(v).strip()
    return default


def navearth_test(url: str, layers: dict) -> list[dict]:
    """Fetch one feature from every configured layer and report what came back."""
    url = check_wfs_url(url)
    out = []
    for key, value in layers.items():
        for name in layer_names(value):
            try:
                feats = _fetch_layer(url, name, limit=1, timeout=20)
                props = sorted((feats[0].get("properties") or {}).keys()) if feats else []
                out.append({"group": key, "layer": name, "ok": True, "message": "reachable" if feats else "reachable, no features",
                            "properties": props[:12]})
            except Exception as e:  # noqa: BLE001
                out.append({"group": key, "layer": name, "ok": False, "message": f"{e.__class__.__name__}: {e}"[:300]})
    return out


def navearth_refresh(db, settings: dict | None = None) -> dict:
    """Load reporting points, navaids, ATS routes and TMA polygons from the NavEarth WFS. Each layer is loaded
    independently; a failing layer is reported and the others are still imported."""
    st = settings or navearth_settings(db)
    url = check_wfs_url(st["url"])
    L = st["layers"]
    out = {"fixes": 0, "navaids": 0, "airways": 0, "tma": 0, "duplicates": 0, "errors": []}
    db.info.pop("fix_cache", None)

    def each(key):
        for name in layer_names(L.get(key, "")):
            try:
                yield from _fetch_layer(url, name)
            except Exception as e:  # noqa: BLE001
                out["errors"].append(f"{name}: {e.__class__.__name__}: {e}"[:300])

    for f in each("points"):
        pr, g = f.get("properties") or {}, f.get("geometry") or {}
        name = _prop(pr, "name_rep", "name", "ident", "designator").upper()
        if name and g.get("type") == "Point" and g.get("coordinates"):
            if upsert_fix(db, name, g["coordinates"][1], g["coordinates"][0], "navearth", "waypoint", _prop(pr, "source_aip_rep", "airac")):
                out["duplicates"] += 1
            else:
                out["fixes"] += 1
    for f in each("navaids"):
        pr, g = f.get("properties") or {}, f.get("geometry") or {}
        name = _prop(pr, "designator_nav", "designator", "ident", "name").upper()
        if name and g.get("type") == "Point" and g.get("coordinates"):
            if upsert_fix(db, name, g["coordinates"][1], g["coordinates"][0], "navearth", "navaid", _prop(pr, "source_aip_nav", "airac")):
                out["duplicates"] += 1
            else:
                out["navaids"] += 1
    segs = {}
    for f in each("routes"):
        pr = f.get("properties") or {}
        name, a, b = _prop(pr, "name_route", "name", "designator").upper(), _prop(pr, "from_name").upper(), _prop(pr, "to_name").upper()
        if name and a and b:
            try:
                sort = float(_prop(pr, "segment_sort", default="0"))
            except ValueError:
                sort = 0.0
            segs.setdefault(name, []).append((sort, a, b))
    for name, lst in segs.items():
        seq = flatten_chains(chain_segments(lst))
        w = db.query(Airway).filter_by(name=name, source="navearth").first()
        if w:
            w.fixes = seq
        else:
            db.add(Airway(name=name, fixes=seq, source="navearth"))
        out["airways"] += 1
    tma = [f for f in each("tma") if (f.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon")]
    if tma or not layer_names(L.get("tma", "")):
        # replace the NavEarth TMA set as a whole: sectors often share a name, so there is no stable key to update
        db.query(Airspace).filter_by(source="navearth", kind="tma").delete()
        for i, f in enumerate(tma):
            pr = f.get("properties") or {}
            name = _prop(pr, "layer", "name_tma", "tma_name", "name", "nama", "designator", "ident",
                         default=str(f.get("id") or f"TMA {i + 1}"))
            code = _prop(pr, "designator", "code", "ident", default=name)
            info = {k: _prop(pr, k) for k in ("class", "lower", "upper", "unt_srvc", "callsign", "freq", "source_aip") if _prop(pr, k)}
            db.add(Airspace(code=code.upper()[:16], name=name[:128], ansp=_prop(pr, "unt_srvc", "ansp", "unit")[:64],
                            kind="tma", source="navearth", props=info, geometry=f["geometry"]))
            out["tma"] += 1
    db.commit()
    return out


def import_airspace(db, data: bytes) -> int:
    gj = json.loads(data)
    feats = gj.get("features", []) if gj.get("type") == "FeatureCollection" else [gj]
    n = 0
    for f in feats:
        pr = f.get("properties", {})
        code = str(pr.get("code") or pr.get("ICAO") or pr.get("id") or pr.get("name") or "").upper()
        if not code:
            continue
        kind = "tma" if str(pr.get("kind") or pr.get("type") or "").lower() == "tma" else "fir"
        a = db.query(Airspace).filter_by(code=code, kind=kind).first() or Airspace(code=code, kind=kind, source="import")
        a.name = str(pr.get("name") or code)
        a.ansp = str(pr.get("ansp") or pr.get("ANSP") or "")
        a.geometry = f.get("geometry") or {}
        db.add(a)
        n += 1
    db.commit()
    return n
