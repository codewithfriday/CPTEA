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
    f = db.query(Fix).filter_by(name=name, source=source).first()
    if f:
        f.lat, f.lon, f.kind, f.airac = lat, lon, kind, airac
    else:
        db.add(Fix(name=name, lat=lat, lon=lon, source=source, kind=kind, airac=airac))


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


def navearth_refresh(db, base: str | None = None) -> dict:
    """FR-DATA-12: fetch fixes, navaids and ATS routes from NavEarth GeoServer (WFS, GeoJSON)."""
    base = base or config.NAVEARTH_WFS
    out = {"fixes": 0, "navaids": 0, "airways": 0}

    def layer(name):
        r = httpx.get(base, params={"service": "WFS", "version": "1.0.0", "request": "GetFeature",
                                    "typeName": f"enroute_chart:{name}", "outputFormat": "application/json"}, timeout=60)
        r.raise_for_status()
        return r.json().get("features", [])

    for f in layer("rep_point"):
        pr, g = f["properties"], f.get("geometry") or {}
        if g.get("coordinates"):
            upsert_fix(db, pr["name_rep"].strip(), g["coordinates"][1], g["coordinates"][0], "navearth", "waypoint",
                       pr.get("source_aip_rep") or "")
            out["fixes"] += 1
    for f in layer("nav_aid_enr"):
        pr, g = f["properties"], f.get("geometry") or {}
        if g.get("coordinates"):
            upsert_fix(db, pr["designator_nav"].strip(), g["coordinates"][1], g["coordinates"][0], "navearth", "navaid",
                       pr.get("source_aip_nav") or "")
            out["navaids"] += 1
    segs = {}
    for lname in ("ats_route_international", "ats_route_rnp_upper"):
        for f in layer(lname):
            pr = f["properties"]
            segs.setdefault(pr["name_route"].strip(), []).append((pr.get("segment_sort") or 0, pr["from_name"].strip(), pr["to_name"].strip()))
    for name, lst in segs.items():
        lst.sort()
        seq = []
        for _, a, b in lst:
            if not seq:
                seq = [a, b]
            elif seq[-1] == a:
                seq.append(b)
            elif b not in seq:
                seq.extend([a, b])
        w = db.query(Airway).filter_by(name=name, source="navearth").first()
        if w:
            w.fixes = seq
        else:
            db.add(Airway(name=name, fixes=seq, source="navearth"))
        out["airways"] += 1
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
        a = db.query(Airspace).filter_by(code=code).first() or Airspace(code=code)
        a.name = str(pr.get("name") or code)
        a.ansp = str(pr.get("ansp") or pr.get("ANSP") or "")
        a.geometry = f.get("geometry") or {}
        db.add(a)
        n += 1
    db.commit()
    return n
