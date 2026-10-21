"""Report generation, review workflow and download (FR-REP-01..08)."""
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import config
from ..db import SessionLocal, get_db
from ..models import Report, Run, User
from ..reports import builder, explained
from ..security import audit, current_user, project_access

router = APIRouter(prefix="/api", tags=["reports"])
STATUS = {}


def rep_out(r: Report, db):
    return {"id": r.id, "run_id": r.run_id, "title": r.title, "options": r.options, "status": r.status,
            "formats": list((r.files or {}).keys()), "created_at": r.created_at,
            "kind": (r.options or {}).get("kind") or "standard",
            "created_by": db.get(User, r.created_by).username if db.get(User, r.created_by) else "",
            "released_by": db.get(User, r.released_by).username if r.released_by and db.get(User, r.released_by) else "",
            "generation": STATUS.get(r.id, "done" if r.files else "pending")}


@router.get("/report-sections")
def sections(user: User = Depends(current_user)):
    return [{"id": s, "title": builder.SECTION_TITLES[s]} for s in builder.SECTIONS]


@router.post("/report-logo")
async def upload_logo(file: UploadFile = File(...), user: User = Depends(current_user)):
    dest = config.upload_dir() / f"logo_{uuid.uuid4().hex[:8]}{Path(file.filename).suffix}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return {"logo_path": str(dest)}


@router.post("/runs/{rid}/reports")
def create_report(rid: int, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.get(Run, rid)
    if not r or r.status != "done":
        raise HTTPException(400, "Run not finished")
    project_access(db, user, r.project_id, "analyst")
    opts = {k: body.get(k) for k in ("title", "authors", "sections", "formats", "filters", "include_drafts", "logo_path")}
    kind = body.get("kind") or "standard"
    if kind not in ("standard", "explained"):
        raise HTTPException(400, "kind must be standard or explained")
    opts["kind"] = kind
    title = body.get("title") or ""
    if kind == "explained":
        # one PDF that explains every table and chart in plain language
        opts["formats"] = ["pdf"]
        if not title:
            from ..models import Project
            p = db.get(Project, r.project_id)
            title = f"Understanding the {p.adep}–{p.ades} results"
        opts["title"] = title
    rep = Report(run_id=rid, title=title, options=opts, created_by=user.id, status="draft")
    db.add(rep)
    db.commit()
    STATUS[rep.id] = "generating"

    def work(rep_id):
        s = SessionLocal()
        try:
            rp = s.get(Report, rep_id)
            gen = explained.generate if (rp.options or {}).get("kind") == "explained" else builder.generate
            rp.files = gen(s, rp, s.get(Run, rp.run_id))
            s.commit()
            STATUS[rep_id] = "done"
        except Exception as e:  # noqa: BLE001
            import traceback
            STATUS[rep_id] = f"error: {e} {traceback.format_exc()[-600:]}"
        finally:
            s.close()

    threading.Thread(target=work, args=(rep.id,), daemon=True).start()
    audit(db, user, "report_generated", "report", rep.id, {"run_id": rid, "options": opts})
    return rep_out(rep, db)


@router.get("/runs/{rid}/reports")
def list_reports(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.get(Run, rid)
    project_access(db, user, r.project_id, "viewer")
    reps = db.query(Report).filter_by(run_id=rid).order_by(Report.id.desc()).all()
    if user.role == "viewer":
        reps = [x for x in reps if x.status == "released"]
    return [rep_out(x, db) for x in reps]


@router.get("/projects/{pid}/reports")
def project_reports(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project_access(db, user, pid, "viewer")
    reps = db.query(Report).join(Run, Run.id == Report.run_id).filter(Run.project_id == pid).order_by(Report.id.desc()).all()
    if user.role == "viewer":
        reps = [x for x in reps if x.status == "released"]
    return [rep_out(x, db) for x in reps]


@router.patch("/reports/{rep_id}")
def set_status(rep_id: int, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-REP-07: Draft -> In review -> Released; only a Reviewer (or admin) releases; released reports are locked."""
    rp = db.get(Report, rep_id)
    r = db.get(Run, rp.run_id)
    new = body.get("status")
    if rp.status == "released":
        raise HTTPException(400, "Released reports are locked")
    if new == "in_review":
        project_access(db, user, r.project_id, "analyst")
    elif new == "released":
        project_access(db, user, r.project_id, "reviewer")
        if user.role not in ("reviewer", "admin"):
            raise HTTPException(403, "Only a Reviewer can release a report")
        if rp.status != "in_review":
            raise HTTPException(400, "Send the report for review first")
        rp.released_by = user.id
    elif new == "draft":
        project_access(db, user, r.project_id, "reviewer")
    else:
        raise HTTPException(400, "Unknown status")
    old = rp.status
    rp.status = new
    db.commit()
    audit(db, user, "report_status", "report", rp.id, {"from": old, "to": new})
    return rep_out(rp, db)


@router.get("/reports/{rep_id}/download/{fmt}")
def download(rep_id: int, fmt: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rp = db.get(Report, rep_id)
    if not rp:
        raise HTTPException(404, "Report not found")
    r = db.get(Run, rp.run_id)
    project_access(db, user, r.project_id, "viewer")
    if user.role == "viewer" and rp.status != "released":
        raise HTTPException(403, "Report not released")
    p = (rp.files or {}).get(fmt)
    if not p or not Path(p).exists():
        raise HTTPException(404, "File not available")
    media = {"pdf": "application/pdf",
             "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}[fmt]
    audit(db, user, "report_downloaded", "report", rp.id, {"fmt": fmt})
    return FileResponse(p, media_type=media, filename=Path(p).name)
