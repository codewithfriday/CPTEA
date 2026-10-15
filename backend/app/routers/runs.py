from pathlib import Path
"""Analysis runs, results, flight view, exports and insights (SRS 5.6, 7, 8.2)."""
import io
import json
import threading

import numpy as np
import pandas as pd
import pyarrow.dataset as pads
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..models import Dataset, Insight, ParameterSet, Project, Run, User
from ..security import audit, current_user, project_access
from ..engine import aggregate, detail, pipeline

router = APIRouter(prefix="/api", tags=["runs"])


def run_out(r: Run, db):
    ps = db.get(ParameterSet, r.paramset_id)
    ds = db.get(Dataset, r.dataset_id)
    return {"id": r.id, "project_id": r.project_id, "name": r.name, "status": r.status, "progress": r.progress,
            "step": r.step, "message": r.message, "dataset_id": r.dataset_id,
            "dataset": f"{ds.name} (v{ds.version})" if ds else "", "paramset_id": r.paramset_id,
            "paramset": f"{ps.name} v{ps.version}" if ps else "", "software_version": r.software_version,
            "summary": r.summary, "user": db.get(User, r.user_id).username if db.get(User, r.user_id) else "",
            "started_at": r.started_at, "finished_at": r.finished_at, "created_at": r.created_at}


class RunIn(BaseModel):
    dataset_id: int
    paramset_id: int
    name: str = ""


@router.post("/projects/{pid}/runs")
def start_run(pid: int, body: RunIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project_access(db, user, pid, "analyst")
    ds = db.get(Dataset, body.dataset_id)
    if not ds or ds.project_id != pid or ds.status != "ready":
        raise HTTPException(400, "Choose a dataset of this project that has finished loading")
    ps = db.get(ParameterSet, body.paramset_id)
    if not ps or (ps.project_id not in (pid, None)):
        raise HTTPException(400, "Choose a parameter set of this project")
    r = Run(project_id=pid, dataset_id=ds.id, paramset_id=ps.id, user_id=user.id,
            name=body.name or f"Run {ds.name} · {ps.name} v{ps.version}", status="queued", message="Queued")
    db.add(r)
    db.commit()
    threading.Thread(target=pipeline.execute, args=(r.id,), daemon=True).start()
    audit(db, user, "run_started", "run", r.id, {"dataset_id": ds.id, "paramset_id": ps.id})
    return run_out(r, db)


@router.get("/projects/{pid}/runs")
def list_runs(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project_access(db, user, pid, "viewer")
    return [run_out(r, db) for r in db.query(Run).filter_by(project_id=pid).order_by(Run.id.desc()).all()]


def get_run(db, user, rid, need="viewer"):
    r = db.get(Run, rid)
    if not r:
        raise HTTPException(404, "Run not found")
    p = project_access(db, user, r.project_id, need)
    return r, p


@router.get("/runs/{rid}")
def run_detail(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r, _ = get_run(db, user, rid)
    o = run_out(r, db)
    mp = config.run_dir(r.id) / "meta.json"
    o["meta"] = json.loads(mp.read_text()) if mp.exists() else {}
    o["params"] = db.get(ParameterSet, r.paramset_id).values
    return o


@router.delete("/runs/{rid}")
def delete_run(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r, _ = get_run(db, user, rid, "analyst")
    if r.status == "running":
        raise HTTPException(400, "Run is still running")
    from ..models import Insight, Report
    db.query(Insight).filter_by(run_id=rid).delete()
    for rep in db.query(Report).filter_by(run_id=rid).all():
        for f in (rep.files or {}).values():
            try:
                Path(f).unlink(missing_ok=True)
            except (OSError, TypeError):
                pass
        db.delete(rep)
    db.delete(r)
    db.commit()
    import shutil
    shutil.rmtree(config.DATA_DIR / "runs" / str(rid), ignore_errors=True)
    audit(db, user, "run_deleted", "run", rid)
    return {"ok": True}


def filters_from(request: Request) -> dict:
    q = request.query_params
    f = {}
    for k in ("dirn", "callsign", "type"):
        v = q.getlist(k)
        if v:
            f[k] = v
    for k in ("cluster", "month_from", "month_to", "season"):
        if q.get(k):
            f[k] = q.get(k)
    return f


@router.get("/runs/{rid}/summary")
def summary(rid: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r, p = get_run(db, user, rid)
    if r.status != "done":
        raise HTTPException(409, "Run not finished")
    f = filters_from(request)
    rd = config.run_dir(r.id)
    if not f and (rd / "summary.json").exists():
        return json.loads((rd / "summary.json").read_text())
    params = db.get(ParameterSet, r.paramset_id).values
    ds = db.get(Dataset, r.dataset_id)
    return json.loads(json.dumps(aggregate.compute(rd, params, p, f, ds.quality), default=float))


@router.get("/runs/{rid}/flights")
def run_flights(rid: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r, _ = get_run(db, user, rid)
    res = pd.read_parquet(config.run_dir(r.id) / "flights.parquet")
    res = aggregate.apply_filters(res, filters_from(request))
    main = int(float(db.get(ParameterSet, r.paramset_id).values["HOR-01"]))
    cols = ["fid", "callsign", "dof", "dirn", "type", "rfl", "cluster", "outlier", "has_plan", "has_ref",
            f"ext_{main}", f"time_{main}", "arr_time_100", "holding", "toc_min", "cruise_changes", "capped_climb",
            "score_total", "time_above_min", "time_below_min"]
    cols = [c for c in cols if c in res]
    out = res[cols].replace({np.nan: None})
    return {"columns": cols, "rows": out.to_dict("records")}


@router.get("/runs/{rid}/flights/{fid}")
def run_flight(rid: int, fid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r, p = get_run(db, user, rid)
    params = db.get(ParameterSet, r.paramset_id).values
    try:
        return json.loads(json.dumps(detail.detail(db, r, p, params, fid), default=lambda o: None if o is None else float(o)))
    except KeyError:
        raise HTTPException(404, "Flight not in this run")


@router.get("/runs/{rid}/tracks")
def run_tracks(rid: int, request: Request, max_points: int = 60, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Downsampled tracks for the density and cluster maps (FR-VIS-31/32)."""
    r, p = get_run(db, user, rid)
    res = aggregate.apply_filters(pd.read_parquet(config.run_dir(r.id) / "flights.parquet"), filters_from(request))
    fids = res.fid.tolist()
    if not fids:
        return {"tracks": []}
    ds = pads.dataset(config.dataset_dir(r.dataset_id) / "points.parquet")
    pts = ds.to_table(columns=["fid", "t", "lat", "lon", "alt"], filter=pads.field("fid").isin(fids)).to_pandas()
    meta = res.set_index("fid")
    out = []
    for fid, g in pts.groupby("fid", sort=False):
        g = g.sort_values("t")
        g = g[g.alt > 0] if (g.alt > 0).sum() > 5 else g
        idx = np.linspace(0, len(g) - 1, min(len(g), max_points)).astype(int)
        m = meta.loc[fid]
        out.append({"fid": fid, "dirn": m.dirn, "cluster": int(m.cluster) if "cluster" in m else 0,
                    "outlier": bool(m.outlier) if "outlier" in m else False, "type": m["type"],
                    "coords": np.c_[g.lat.values[idx].round(4), g.lon.values[idx].round(4)].tolist()})
    return {"tracks": out, "airports": {p.adep: [p.adep_lat, p.adep_lon], p.ades: [p.ades_lat, p.ades_lon]}}


@router.get("/runs/{rid}/export")
def export(rid: int, fmt: str = "xlsx", user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-ANA-64: per-flight and aggregated results as CSV or Excel."""
    r, p = get_run(db, user, rid)
    rd = config.run_dir(r.id)
    res = pd.read_parquet(rd / "flights.parquet")
    S = json.loads((rd / "summary.json").read_text())
    if fmt == "csv":
        buf = io.StringIO()
        res.to_csv(buf, index=False)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename=cptea_run{rid}_flights.csv"})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        res.to_excel(xw, sheet_name="flights", index=False)
        for key, sheet in (("extension", "extension"), ("enroute_monthly", "enroute_monthly"), ("variance", "variance"),
                           ("lateral", "lateral"), ("fir", "fir")):
            if S.get(key):
                pd.DataFrame(S[key]).to_excel(xw, sheet_name=sheet, index=False)
        pd.DataFrame(S["terminal"]["by_ades"]).to_excel(xw, sheet_name="terminal", index=False)
        pd.DataFrame(S["terminal"]["monthly"]).to_excel(xw, sheet_name="terminal_monthly", index=False)
        for k in ("classes", "score", "capped"):
            if S["cruise"].get(k):
                pd.DataFrame(S["cruise"][k]).to_excel(xw, sheet_name=f"cruise_{k}", index=False)
        for k in ("primary", "sensitivity", "bands"):
            if S["leveloffs"].get(k):
                pd.DataFrame(S["leveloffs"][k]).to_excel(xw, sheet_name=f"leveloffs_{k}", index=False)
        for k in ("levels", "changes", "waypoints"):
            if (rd / f"{k}.parquet").exists():
                pd.read_parquet(rd / f"{k}.parquet").to_excel(xw, sheet_name=k, index=False)
        pd.DataFrame([{"parameter": k, "value": json.dumps(v)} for k, v in db.get(ParameterSet, r.paramset_id).values.items()]).to_excel(xw, sheet_name="parameters", index=False)
    buf.seek(0)
    audit(db, user, "run_exported", "run", rid, {"fmt": fmt})
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename=cptea_run{rid}.xlsx"})


@router.get("/runs/compare/{a}/{b}")
def compare(a: int, b: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-ANA-63: compare two runs metric by metric."""
    out = []
    ra, _ = get_run(db, user, a)
    rb, _ = get_run(db, user, b)
    Sa = json.loads((config.run_dir(a) / "summary.json").read_text())
    Sb = json.loads((config.run_dir(b) / "summary.json").read_text())
    ka = {k["id"]: k for k in Sa["kpis"]}
    for k in Sb["kpis"]:
        va = ka.get(k["id"], {}).get("value")
        out.append({"metric": k["label"], "unit": k["unit"], "a": va, "b": k["value"],
                    "diff": (k["value"] - va) if isinstance(va, (int, float)) and isinstance(k["value"], (int, float)) else None})
    pa = db.get(ParameterSet, ra.paramset_id).values
    pb = db.get(ParameterSet, rb.paramset_id).values
    pdiff = [{"parameter": k, "a": pa.get(k), "b": pb.get(k)} for k in sorted(set(pa) | set(pb)) if pa.get(k) != pb.get(k)]
    return {"metrics": out, "parameters": pdiff}


# ----------------------------------------------------------------- insights
def ins_out(i: Insight, db):
    return {"id": i.id, "rule": i.rule, "title": i.title, "drafted": i.drafted, "edited": i.edited,
            "text": i.edited or i.drafted, "numbers": i.numbers, "chart": i.chart, "effect": i.effect,
            "status": i.status, "kind": i.kind,
            "editor": db.get(User, i.editor_id).username if i.editor_id and db.get(User, i.editor_id) else "",
            "updated_at": i.updated_at}


@router.get("/runs/{rid}/insights")
def list_insights(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_run(db, user, rid)
    return [ins_out(i, db) for i in db.query(Insight).filter_by(run_id=rid).order_by(Insight.effect.desc(), Insight.id).all()]


@router.post("/runs/{rid}/insights")
def add_insight(rid: int, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-REP-20: free-text insights and recommendations."""
    get_run(db, user, rid, "analyst")
    i = Insight(run_id=rid, rule="MANUAL", title=body.get("title", ""), drafted="", edited=body.get("text", ""),
                status="accepted", kind=body.get("kind", "insight"), editor_id=user.id, effect=float(body.get("effect", 0)))
    db.add(i)
    db.commit()
    audit(db, user, "insight_added", "insight", i.id, {"run_id": rid})
    return ins_out(i, db)


@router.patch("/insights/{iid}")
def update_insight(iid: int, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    i = db.get(Insight, iid)
    r = db.get(Run, i.run_id)
    need = "reviewer" if set(body) <= {"comment"} else "analyst"
    project_access(db, user, r.project_id, need)
    before = {"status": i.status, "edited": i.edited}
    if "edited" in body:
        i.edited = body["edited"]
        if i.status in ("drafted", "accepted") and body["edited"] and body["edited"] != i.drafted:
            i.status = "edited"
    if "status" in body:
        if body["status"] not in ("drafted", "accepted", "edited", "rejected"):
            raise HTTPException(400, "Invalid status")
        i.status = body["status"]
    if "title" in body:
        i.title = body["title"]
    i.editor_id = user.id
    db.commit()
    audit(db, user, "insight_updated", "insight", i.id, {"before": before, "after": {"status": i.status, "edited": i.edited}})
    return ins_out(i, db)


@router.delete("/insights/{iid}")
def delete_insight(iid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    i = db.get(Insight, iid)
    r = db.get(Run, i.run_id)
    project_access(db, user, r.project_id, "analyst")
    if i.rule != "MANUAL":
        raise HTTPException(400, "Rule-drafted insights are rejected, not deleted")
    db.delete(i)
    db.commit()
    return {"ok": True}
