import json
import shutil
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..defaults import DEFAULT_SET_NAME, DEFAULT_VALUES, PARAMS, TABS, diff_from_default, validate
from ..models import (Dataset, Fix, Flight, FlightPlanRecord, ParameterSet, Project, ProjectMember, Run, User)
from ..security import ROLE_RANK, audit, current_user, project_access, require_role
from ..engine import ingest

router = APIRouter(prefix="/api", tags=["projects"])


def project_out(p: Project, db: Session, user: User | None = None):
    return {"id": p.id, "name": p.name, "description": p.description, "adep": p.adep, "ades": p.ades,
            "adep_lat": p.adep_lat, "adep_lon": p.adep_lon, "ades_lat": p.ades_lat, "ades_lon": p.ades_lon,
            "period_start": p.period_start, "period_end": p.period_end, "callsigns": p.callsigns,
            "owner_id": p.owner_id, "owner": (db.get(User, p.owner_id).username if p.owner_id else ""),
            "created_at": p.created_at, "updated_at": p.updated_at,
            "n_datasets": db.query(Dataset).filter_by(project_id=p.id).count(),
            "n_runs": db.query(Run).filter_by(project_id=p.id).count(),
            "members": [{"user_id": m.user_id, "level": m.level,
                         "username": db.get(User, m.user_id).username if db.get(User, m.user_id) else ""} for m in p.members]}


class ProjectIn(BaseModel):
    name: str
    description: str = ""
    adep: str
    ades: str
    adep_lat: float | None = None
    adep_lon: float | None = None
    ades_lat: float | None = None
    ades_lon: float | None = None
    period_start: str = ""
    period_end: str = ""
    callsigns: dict = {}
    copy_paramset_id: int | None = None


def _ap_coords(db, code):
    f = db.query(Fix).filter(Fix.name == code).first()
    return (f.lat, f.lon) if f else (None, None)


@router.get("/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = []
    for p in db.query(Project).order_by(Project.updated_at.desc()).all():
        try:
            project_access(db, user, p.id, "viewer")
        except HTTPException:
            continue
        out.append(project_out(p, db, user))
    return out


@router.post("/projects")
def create_project(body: ProjectIn, user: User = Depends(require_role("analyst")), db: Session = Depends(get_db)):
    adep, ades = body.adep.strip().upper(), body.ades.strip().upper()
    a = (body.adep_lat, body.adep_lon) if body.adep_lat is not None else _ap_coords(db, adep)
    b = (body.ades_lat, body.ades_lon) if body.ades_lat is not None else _ap_coords(db, ades)
    if None in a or None in b:
        raise HTTPException(400, "Aerodrome coordinates are required (not found in the AIS data)")
    p = Project(name=body.name, description=body.description, adep=adep, ades=ades, adep_lat=a[0], adep_lon=a[1],
                ades_lat=b[0], ades_lon=b[1], period_start=body.period_start, period_end=body.period_end,
                callsigns={k.strip().upper(): v for k, v in body.callsigns.items()}, owner_id=user.id)
    db.add(p)
    db.commit()
    base = db.get(ParameterSet, body.copy_paramset_id) if body.copy_paramset_id else \
        db.query(ParameterSet).filter(ParameterSet.project_id.is_(None)).first()
    db.add(ParameterSet(project_id=p.id, name=f"{p.name} parameters", version=1,
                        values=dict(base.values if base else DEFAULT_VALUES), note=f"Copied from {base.name if base else DEFAULT_SET_NAME}",
                        parent_id=base.id if base else None, author_id=user.id))
    db.commit()
    audit(db, user, "project_created", "project", p.id, {"name": p.name})
    return project_out(p, db, user)


@router.get("/projects/{pid}")
def get_project(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return project_out(project_access(db, user, pid, "viewer"), db, user)


@router.patch("/projects/{pid}")
def update_project(pid: int, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_access(db, user, pid, "analyst")
    before = project_out(p, db)
    for k in ("name", "description", "period_start", "period_end", "adep_lat", "adep_lon", "ades_lat", "ades_lon"):
        if k in body:
            setattr(p, k, body[k])
    if "callsigns" in body:
        p.callsigns = {k.strip().upper(): v for k, v in body["callsigns"].items()}
    db.commit()
    audit(db, user, "project_updated", "project", p.id, {"before": json.loads(json.dumps(before, default=str)), "changes": body})
    return project_out(p, db)


@router.delete("/projects/{pid}")
def delete_project(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_access(db, user, pid, "analyst")
    if user.role != "admin" and p.owner_id != user.id:
        raise HTTPException(403, "Only the owner or an administrator can delete a project")
    audit(db, user, "project_deleted", "project", p.id, {"name": p.name})
    db.delete(p)
    db.commit()
    return {"ok": True}


class MemberIn(BaseModel):
    user_id: int
    level: str = "viewer"


@router.post("/projects/{pid}/members")
def add_member(pid: int, body: MemberIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_access(db, user, pid, "analyst")
    if body.level not in ("viewer", "reviewer", "analyst"):
        raise HTTPException(400, "Level must be viewer, reviewer or analyst")
    m = db.query(ProjectMember).filter_by(project_id=pid, user_id=body.user_id).first() or ProjectMember(project_id=pid, user_id=body.user_id)
    m.level = body.level
    db.add(m)
    db.commit()
    audit(db, user, "project_shared", "project", pid, {"user_id": body.user_id, "level": body.level})
    return project_out(p, db)


@router.delete("/projects/{pid}/members/{uid}")
def remove_member(pid: int, uid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_access(db, user, pid, "analyst")
    db.query(ProjectMember).filter_by(project_id=pid, user_id=uid).delete()
    db.commit()
    audit(db, user, "project_unshared", "project", pid, {"user_id": uid})
    return project_out(p, db)


# ------------------------------------------------------------- datasets
def dataset_out(d: Dataset, full=False):
    o = {"id": d.id, "project_id": d.project_id, "version": d.version, "name": d.name, "status": d.status,
         "message": d.message, "n_files": d.n_files, "n_flights": d.n_flights, "content_hash": d.content_hash,
         "created_at": d.created_at}
    if full:
        o["quality"] = d.quality
        o["load_log"] = d.load_log
    return o


def project_params(db, pid):
    ps = db.query(ParameterSet).filter_by(project_id=pid).order_by(ParameterSet.id.desc()).first()
    return ps.values if ps else dict(DEFAULT_VALUES)


@router.get("/projects/{pid}/datasets")
def list_datasets(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project_access(db, user, pid, "viewer")
    return [dataset_out(d) for d in db.query(Dataset).filter_by(project_id=pid).order_by(Dataset.id.desc()).all()]


@router.post("/projects/{pid}/datasets")
async def create_dataset(pid: int, name: str = Form(""), server_path: str = Form(""),
                         files: list[UploadFile] = File(default=[]), user: User = Depends(current_user),
                         db: Session = Depends(get_db)):
    """FR-DATA-01: files, a ZIP, or a folder path on the server."""
    project_access(db, user, pid, "analyst")
    ver = (db.query(func.max(Dataset.version)).filter_by(project_id=pid).scalar() or 0) + 1
    ds = Dataset(project_id=pid, version=ver, name=name or f"Tracks v{ver}", created_by=user.id, status="loading",
                 message="Queued")
    db.add(ds)
    db.commit()
    sources = []
    if server_path:
        sp = Path(server_path)
        if not sp.exists():
            ds.status, ds.message = "error", f"Server path not found: {server_path}"
            db.commit()
            raise HTTPException(400, ds.message)
        sources.append(sp)
    if files:
        up = config.dataset_dir(ds.id) / "upload"
        up.mkdir(exist_ok=True)
        for f in files:
            dest = up / Path(f.filename).name if not f.filename.endswith(".csv") else up / f.filename.replace("/", "__")
            with dest.open("wb") as out:
                shutil.copyfileobj(f.file, out)
        sources.append(up)
    if not sources:
        raise HTTPException(400, "Provide files, a ZIP or a server folder path")
    params = project_params(db, pid)
    threading.Thread(target=ingest.load_tracks, args=(ds.id, sources, params), daemon=True).start()
    audit(db, user, "dataset_created", "dataset", ds.id, {"project_id": pid, "server_path": server_path,
                                                          "files": len(files)})
    return dataset_out(ds)


@router.get("/datasets/{did}")
def get_dataset(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = db.get(Dataset, did)
    if not d:
        raise HTTPException(404, "Dataset not found")
    project_access(db, user, d.project_id, "viewer")
    o = dataset_out(d, full=True)
    fl = db.query(Flight).filter_by(dataset_id=did).all()
    plans = {(r.callsign, r.dof) for r in db.query(FlightPlanRecord).filter_by(project_id=d.project_id).all()}
    o["fpl_match"] = {"matched": sum((f.callsign, f.dof) in plans for f in fl), "flights": len(fl)}
    o["included"] = sum(f.included for f in fl)
    return o


@router.get("/datasets/{did}/flights")
def dataset_flights(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = db.get(Dataset, did)
    project_access(db, user, d.project_id, "viewer")
    return [{"id": f.id, "fid": f.fid, "callsign": f.callsign, "dof": f.dof, "adep": f.adep, "ades": f.ades,
             "n_points": f.n_points, "duration_min": round(f.duration_min, 1), "gate_to_gate": f.gate_to_gate,
             "flags": f.flags, "included": f.included, "exclude_reason": f.exclude_reason}
            for f in db.query(Flight).filter_by(dataset_id=did).order_by(Flight.dof, Flight.callsign).all()]


@router.patch("/flights/{fid_id}")
def set_flight_inclusion(fid_id: int, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-DATA-15: manual exclusion with a reason."""
    f = db.get(Flight, fid_id)
    d = db.get(Dataset, f.dataset_id)
    project_access(db, user, d.project_id, "analyst")
    inc = bool(body.get("included"))
    flags = [x for x in (f.flags or []) if x != "manual_exclude"]
    if not inc:
        if not body.get("reason"):
            raise HTTPException(400, "A reason is required to exclude a flight")
        flags.append("manual_exclude")
        f.exclude_reason = body["reason"]
    else:
        f.exclude_reason = ""
    f.flags, f.included = flags, inc
    db.commit()
    audit(db, user, "flight_inclusion", "flight", f.fid, {"included": inc, "reason": body.get("reason", "")})
    return {"ok": True}


@router.delete("/datasets/{did}")
def delete_dataset(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = db.get(Dataset, did)
    project_access(db, user, d.project_id, "analyst")
    if db.query(Run).filter_by(dataset_id=did).count():
        raise HTTPException(400, "Dataset is used by analysis runs; delete the runs first")
    db.query(Flight).filter_by(dataset_id=did).delete()
    db.delete(d)
    db.commit()
    shutil.rmtree(config.DATA_DIR / "datasets" / str(did), ignore_errors=True)
    audit(db, user, "dataset_deleted", "dataset", did)
    return {"ok": True}


# ------------------------------------------------------------- parameter sets (FR-SET)
@router.get("/params/definitions")
def definitions(user: User = Depends(current_user)):
    return {"tabs": TABS, "params": list(PARAMS.values()), "defaults": DEFAULT_VALUES, "default_name": DEFAULT_SET_NAME}


def ps_out(ps: ParameterSet, db):
    return {"id": ps.id, "project_id": ps.project_id, "name": ps.name, "version": ps.version, "values": ps.values,
            "note": ps.note, "readonly": ps.readonly, "parent_id": ps.parent_id, "created_at": ps.created_at,
            "author": db.get(User, ps.author_id).username if ps.author_id and db.get(User, ps.author_id) else "system",
            "diff": diff_from_default(ps.values)}


@router.get("/paramsets/system")
def system_sets(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [ps_out(p, db) for p in db.query(ParameterSet).filter(ParameterSet.project_id.is_(None)).order_by(ParameterSet.id).all()]


@router.post("/paramsets/system")
def save_system(body: dict, admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    """FR-SET-09: only administrators change the system-wide default set (saved as a new version)."""
    cur = db.query(ParameterSet).filter(ParameterSet.project_id.is_(None)).order_by(ParameterSet.id.desc()).first()
    vals = validate(body.get("values", {}), True, cur.values if cur else None)
    ps = ParameterSet(project_id=None, name=body.get("name") or DEFAULT_SET_NAME, version=(cur.version + 1 if cur else 1),
                      values=vals, note=body.get("note", ""), readonly=True, parent_id=cur.id if cur else None, author_id=admin.id)
    db.add(ps)
    db.commit()
    audit(db, admin, "system_params_saved", "paramset", ps.id, {"diff": diff_from_default(vals)})
    return ps_out(ps, db)


@router.get("/projects/{pid}/paramsets")
def list_sets(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project_access(db, user, pid, "viewer")
    return [ps_out(p, db) for p in db.query(ParameterSet).filter_by(project_id=pid).order_by(ParameterSet.id.desc()).all()]


@router.post("/projects/{pid}/paramsets")
def save_set(pid: int, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-SET-05: saving creates a new version; earlier versions stay read-only."""
    project_access(db, user, pid, "analyst")
    parent = db.get(ParameterSet, body["parent_id"]) if body.get("parent_id") else \
        db.query(ParameterSet).filter_by(project_id=pid).order_by(ParameterSet.id.desc()).first()
    vals = validate(body.get("values", {}), user.role == "admin", parent.values if parent else None)
    name = body.get("name") or (parent.name if parent else "Parameters")
    ver = (db.query(func.max(ParameterSet.version)).filter_by(project_id=pid, name=name).scalar() or 0) + 1
    ps = ParameterSet(project_id=pid, name=name, version=ver, values=vals, note=body.get("note", ""),
                      parent_id=parent.id if parent else None, author_id=user.id)
    db.add(ps)
    db.commit()
    changed = {k: v for k, v in vals.items() if not parent or parent.values.get(k) != v}
    audit(db, user, "params_saved", "paramset", ps.id, {"project_id": pid, "changed": changed, "note": ps.note})
    return ps_out(ps, db)


@router.get("/paramsets/{psid}")
def get_set(psid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ps = db.get(ParameterSet, psid)
    if not ps:
        raise HTTPException(404, "Parameter set not found")
    if ps.project_id:
        project_access(db, user, ps.project_id, "viewer")
    return ps_out(ps, db)


@router.get("/paramsets/{psid}/export")
def export_set(psid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-SET-07: JSON export for sharing in WG3."""
    ps = db.get(ParameterSet, psid)
    if ps.project_id:
        project_access(db, user, ps.project_id, "viewer")
    p = config.DATA_DIR / f"paramset_{ps.id}.json"
    p.write_text(json.dumps({"cptea_paramset": 1, "name": ps.name, "version": ps.version, "values": ps.values,
                             "note": ps.note}, indent=2, ensure_ascii=False))
    return FileResponse(p, filename=f"{ps.name.replace(' ', '_')}_v{ps.version}.json", media_type="application/json")


@router.post("/projects/{pid}/paramsets/import")
async def import_set(pid: int, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    project_access(db, user, pid, "analyst")
    data = json.loads((await file.read()).decode("utf-8"))
    vals = validate(data.get("values", {}), user.role == "admin", project_params(db, pid))
    name = f"{data.get('name', 'Imported')} (imported)"
    ps = ParameterSet(project_id=pid, name=name, version=1, values=vals, note=f"Imported from {file.filename}", author_id=user.id)
    db.add(ps)
    db.commit()
    audit(db, user, "params_imported", "paramset", ps.id, {"file": file.filename})
    return ps_out(ps, db)
