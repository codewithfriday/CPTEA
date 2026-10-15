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
    return [{"id": a.id, "name": a.name, "source": a.source, "fixes": a.fixes} for a in db.query(Airway).order_by(Airway.name).all()]


@router.get("/ais/geojson")
def ais_geojson(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Map layers: fixes, airways and airspaces."""
    fx = {f.name: (f.lat, f.lon) for f in db.query(Fix).all()}
    feats = []
    for a in db.query(Airway).all():
        coords = [[fx[n][1], fx[n][0]] for n in a.fixes if n in fx]
        if len(coords) > 1:
            feats.append({"type": "Feature", "properties": {"layer": "airway", "name": a.name, "source": a.source},
                          "geometry": {"type": "LineString", "coordinates": coords}})
    for s in db.query(Airspace).all():
        feats.append({"type": "Feature", "properties": {"layer": "airspace", "name": s.code, "ansp": s.ansp}, "geometry": s.geometry})
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


@router.post("/ais/navearth-refresh")
def navearth(admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    try:
        res = ingest.navearth_refresh(db)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"NavEarth not reachable from the server: {e}")
    audit(db, admin, "navearth_refreshed", "ais", "", res)
    return res
