"""Flight plans (file and API) and aeronautical data endpoints."""
import shutil
import threading
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..models import Airspace, Airway, Dataset, Fix, FlightPlanRecord, User
from ..security import audit, current_user, project_access, require_role
from ..engine import ingest
from .projects import project_params

router = APIRouter(prefix="/api", tags=["data"])
JOBS: dict[str, dict] = {}


@router.post("/projects/{pid}/fpl/upload")
async def upload_fpl(pid: int, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    project_access(db, user, pid, "analyst")
    dest = config.upload_dir() / f"{uuid.uuid4().hex}_{file.filename}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    try:
        res = ingest.import_fpl_file(db, pid, dest)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read flight plan file: {e}")
    audit(db, user, "fpl_imported", "project", pid, {"file": file.filename, **res})
    return res


@router.get("/projects/{pid}/fpl/stats")
def fpl_stats(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project_access(db, user, pid, "viewer")
    recs = db.query(FlightPlanRecord).filter_by(project_id=pid).all()
    by_src = {}
    for r in recs:
        s = by_src.setdefault(r.source, {"messages": 0, "flight_days": set(), "types": {}})
        s["messages"] += 1
        s["flight_days"].add((r.callsign, r.dof))
        t = r.parsed.get("type") or ""
        if t:
            s["types"][t] = s["types"].get(t, 0) + 1
    for s in by_src.values():
        s["flight_days"] = len(s["flight_days"])
    return {"sources": by_src, "total": len(recs)}


@router.get("/projects/{pid}/fpl")
def fpl_list(pid: int, callsign: str = "", dof: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    project_access(db, user, pid, "viewer")
    q = db.query(FlightPlanRecord).filter_by(project_id=pid)
    if callsign:
        q = q.filter_by(callsign=callsign.upper())
    if dof:
        q = q.filter_by(dof=dof)
    return [{"id": r.id, "callsign": r.callsign, "dof": r.dof, "source": r.source, "msg_type": r.msg_type,
             "filed_time": r.filed_time, "raw": r.raw, "parsed": r.parsed, "atd": r.atd, "ata": r.ata, "status": r.status}
            for r in q.order_by(FlightPlanRecord.filed_time).limit(200).all()]


@router.get("/fpl/api-status")
def api_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    base = config.FLIGHT_API_URL
    ok, msg = ingest.api_reachable(base)
    return {"ok": ok, "message": msg, "base": base}


@router.post("/projects/{pid}/fpl/fetch")
def fetch_api(pid: int, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-DATA-21..29: fetch plans per flight from the AirNav flight data API in the background."""
    project_access(db, user, pid, "analyst")
    did = body.get("dataset_id")
    if not did or not db.get(Dataset, did):
        raise HTTPException(400, "dataset_id is required")
    params = project_params(db, pid)
    job = uuid.uuid4().hex[:12]
    JOBS[job] = {"status": "running", "result": None}

    def work():
        try:
            JOBS[job]["result"] = ingest.fetch_fpl_api(pid, did, params, bool(body.get("force")), body.get("fid"))
            JOBS[job]["status"] = "done"
        except Exception as e:  # noqa: BLE001
            JOBS[job] = {"status": "error", "result": {"ok": False, "message": str(e)}}

    threading.Thread(target=work, daemon=True).start()
    audit(db, user, "fpl_api_fetch", "project", pid, {"dataset_id": did, "force": bool(body.get("force")), "fid": body.get("fid")})
    return {"job": job}


@router.get("/jobs/{job}")
def job_status(job: str, user: User = Depends(current_user)):
    if job not in JOBS:
        raise HTTPException(404, "Unknown job")
    return JOBS[job]


# ------------------------------------------------------------ AIS
@router.get("/ais/fixes")
def fixes(q: str = "", limit: int = 200, user: User = Depends(current_user), db: Session = Depends(get_db)):
    qq = db.query(Fix)
    if q:
        qq = qq.filter(Fix.name.contains(q.upper()))
    return [{"id": f.id, "name": f.name, "lat": f.lat, "lon": f.lon, "kind": f.kind, "source": f.source, "airac": f.airac}
            for f in qq.order_by(Fix.name).limit(min(limit, 5000)).all()]


@router.post("/ais/fixes")
def add_fix(body: dict, user: User = Depends(require_role("analyst")), db: Session = Depends(get_db)):
    """FR-DATA-11: the Analyst adds coordinates for an unresolved fix."""
    name = body["name"].strip().upper()
    ingest.upsert_fix(db, name, float(body["lat"]), float(body["lon"]), "manual", body.get("kind", "waypoint"), body.get("airac", ""))
    db.commit()
    audit(db, user, "fix_added", "fix", name, body)
    return {"ok": True}


@router.get("/ais/airways")
def airways(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": a.id, "name": a.name, "source": a.source, "fixes": [f for f in a.fixes if f], "chains": ingest.split_chains(a.fixes)}
            for a in db.query(Airway).order_by(Airway.name, Airway.source).all()]


@router.get("/ais/geojson")
def ais_geojson(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Map layers: fixes, airways and airspaces."""
    fx = {f.name: (f.lat, f.lon) for f in db.query(Fix).all()}
    feats = []
    for a in db.query(Airway).all():
        lines = [[[fx[n][1], fx[n][0]] for n in c if n in fx] for c in ingest.split_chains(a.fixes)]
        lines = [ln for ln in lines if len(ln) > 1]
        if lines:
            feats.append({"type": "Feature", "properties": {"layer": "airway", "name": a.name, "source": a.source},
                          "geometry": {"type": "MultiLineString", "coordinates": lines}})
    for s in db.query(Airspace).all():
        feats.append({"type": "Feature", "properties": {"layer": "tma" if s.kind == "tma" else "airspace", "name": s.name or s.code,
                                                        "code": s.code, "ansp": s.ansp, **(s.props or {})}, "geometry": s.geometry})
    return {"type": "FeatureCollection", "features": feats,
            "fixes": [{"name": n, "lat": v[0], "lon": v[1]} for n, v in fx.items()]}


@router.post("/ais/import")
async def ais_import(kind: str = Form(...), file: UploadFile = File(...), user: User = Depends(require_role("analyst")),
                     db: Session = Depends(get_db)):
    data = await file.read()
    try:
        if kind == "fixes":
            n = ingest.import_fixes_csv(db, data)
        elif kind == "airways":
            n = ingest.import_airways_csv(db, data)
        elif kind == "airspace":
            if user.role != "admin":
                raise HTTPException(403, "Only administrators import airspace boundaries")
            n = ingest.import_airspace(db, data)
        else:
            raise HTTPException(400, "kind must be fixes, airways or airspace")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Import failed: {e}")
    audit(db, user, "ais_imported", kind, file.filename, {"count": n})
    return {"imported": n}


@router.get("/settings/navearth")
def get_navearth(user: User = Depends(current_user), db: Session = Depends(get_db)):
    st = ingest.navearth_settings(db)
    return {**st, "default": ingest.navearth_defaults(), "labels": ingest.LAYER_LABELS}


@router.put("/settings/navearth")
def put_navearth(body: dict, admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    """Administrators change the NavEarth WFS address and layer names; an empty body restores the defaults."""
    from ..models import AppSetting, now
    before = ingest.navearth_settings(db)
    row = db.get(AppSetting, ingest.NAVEARTH_KEY)
    if body.get("reset"):
        if row:
            db.delete(row)
    else:
        try:
            url = ingest.check_wfs_url(body.get("url", ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        layers = {k: ", ".join(ingest.layer_names(str(v))) for k, v in (body.get("layers") or {}).items() if k in ingest.DEFAULT_LAYERS}
        if not row:
            row = AppSetting(key=ingest.NAVEARTH_KEY)
            db.add(row)
        row.value = {"url": url, "layers": {**before["layers"], **layers}}
        row.updated_by, row.updated_at = admin.username, now()
    db.commit()
    after = ingest.navearth_settings(db)
    audit(db, admin, "navearth_settings_changed", "setting", ingest.NAVEARTH_KEY,
          {"before": {"url": before["url"], "layers": before["layers"]}, "after": {"url": after["url"], "layers": after["layers"]}})
    return {**after, "default": ingest.navearth_defaults(), "labels": ingest.LAYER_LABELS}


@router.post("/ais/navearth-test")
def navearth_test(body: dict, admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    """Try an address (saved or not) by fetching one feature per layer."""
    st = ingest.navearth_settings(db)
    try:
        return {"results": ingest.navearth_test(body.get("url") or st["url"], {**st["layers"], **(body.get("layers") or {})})}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/ais/navearth-refresh")
def navearth(admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    st = ingest.navearth_settings(db)
    try:
        res = ingest.navearth_refresh(db, st)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001  (layer download errors are collected per layer; this is a storage error)
        db.rollback()
        raise HTTPException(500, f"NavEarth data was downloaded but could not be saved; nothing was changed. {e.__class__.__name__}: {str(e)[:300]}")
    finally:
        db.info.pop("fix_cache", None)
    if not (res["fixes"] or res["navaids"] or res["airways"] or res["tma"]) and res["errors"]:
        raise HTTPException(502, "NavEarth not reachable from the server: " + "; ".join(res["errors"][:3]))
    audit(db, admin, "navearth_refreshed", "ais", st["url"], res)
    return res
