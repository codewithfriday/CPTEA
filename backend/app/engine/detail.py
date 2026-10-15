"""Flight view data (FR-VIS-10..16): one trajectory against its plan."""
import json

import numpy as np
import pandas as pd
import pyarrow.dataset as pads

from .. import config
from ..models import Flight
from .analysis import Ctx, plan_geometry
from .geo import dist_fn, gc
from .pipeline import build_plans, load_navdb
from .tracks import resample, segments


def _clean(v):
    if isinstance(v, (np.floating, float)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def flight_points(dataset_id: int, fid: str) -> pd.DataFrame:
    d = pads.dataset(config.dataset_dir(dataset_id) / "points.parquet")
    return d.to_table(filter=pads.field("fid") == fid).to_pandas().sort_values("t").reset_index(drop=True)


def detail(db, run, project, params: dict, fid: str) -> dict:
    rd = config.run_dir(run.id)
    res = pd.read_parquet(rd / "flights.parquet")
    row = res[res.fid == fid]
    if row.empty:
        raise KeyError(fid)
    r = row.iloc[0]
    tr = flight_points(run.dataset_id, fid)
    f = db.query(Flight).filter_by(dataset_id=run.dataset_id, fid=fid).first()
    fixes, airways = load_navdb(db)
    plans, _ = build_plans(db, project.id, pd.DataFrame([{"fid": fid, "callsign": f.callsign, "dof": f.dof,
                                                          "adep": f.adep, "ades": f.ades}]), params, fixes, airways)
    plan = plans.get(fid)
    ctx = Ctx(params, project, dist_fn(params["GEN-01"]))
    geom = plan_geometry(plan, f.adep, f.ades, ctx, fixes) if plan and plan.get("usable") else None
    air = tr[tr.alt > 0]
    rs = resample(air, float(params["GEN-06"]), float(params["GEN-07"]), float(params["VER-01"]))
    t0 = rs["t"][0]
    step = float(params["GEN-06"])
    lvl = (np.abs(rs["vr"]) <= float(params["VER-02"])) & (rs["alt"] > 1000)
    segs = [(a, b) for a, b in segments(lvl) if (b - a) * step >= float(params["VER-03"])]
    # planned level along the track (cruise legs, after TOC)
    planned = np.full(len(rs["alt"]), np.nan)
    along = np.full(len(rs["alt"]), np.nan)
    if geom is not None:
        rl, ro, rcum = geom["rl"], geom["ro"], geom["rcum"]
        for s0 in range(0, len(rs["lat"]), 300):
            d = gc(rs["lat"][s0:s0 + 300, None], rs["lon"][s0:s0 + 300, None], rl[None, :], ro[None, :])
            k = d.argmin(1)
            along[s0:s0 + 300] = rcum[k]
        fix_cum = np.r_[0.0, geom["fix_cum"]]
        lv = [geom["rfl"]] + list(geom["fix_levels"])
        leg = np.clip(np.searchsorted(fix_cum, along, side="right") - 1, 0, len(lv) - 1)
        planned = np.array([lv[i] if lv[i] is not None else np.nan for i in leg], float)
    toc_i = int(np.argmin(np.abs((rs["t"] - t0) / 60 - (r.get("toc_min") or 0)))) if pd.notna(r.get("toc_min")) else None
    tod_i = len(rs["t"]) - 1 - int((r.get("tod_to_end_min") or 0) * 60 / step) if pd.notna(r.get("tod_to_end_min")) else None
    if toc_i is not None:
        planned[:toc_i] = np.nan
    if tod_i is not None and 0 <= tod_i < len(planned):
        planned[tod_i:] = np.nan
    k = max(1, len(rs["t"]) // 900)
    prof = {"t_min": ((rs["t"] - t0) / 60)[::k].round(2).tolist(), "alt": rs["alt"][::k].round(0).tolist(),
            "vr": [None if np.isnan(x) else round(float(x), 2) for x in rs["vr"][::k]],
            "planned": [None if np.isnan(x) else float(x) for x in planned[::k]],
            "gap": rs["gap"][::k].tolist(),
            "dist_ades": gc(rs["lat"], rs["lon"], project.ades_lat if f.ades == project.ades else project.adep_lat,
                            project.ades_lon if f.ades == project.ades else project.adep_lon)[::k].round(1).tolist()}
    k2 = max(1, len(tr) // 1500)
    track = {"lat": tr.lat.values[::k2].round(5).tolist(), "lon": tr.lon.values[::k2].round(5).tolist(),
             "alt": tr.alt.values[::k2].tolist(), "t_min": ((tr.t.values[::k2] - tr.t.values[0]) / 60).round(2).tolist(),
             "gs": [None if pd.isna(x) else float(x) for x in tr.gs.values[::k2]]}
    dt = np.diff(tr.t.values)
    gi = np.where(dt > float(params["GEN-04"]))[0]
    gaps = [{"lat": float((tr.lat.iloc[i] + tr.lat.iloc[i + 1]) / 2), "lon": float((tr.lon.iloc[i] + tr.lon.iloc[i + 1]) / 2),
             "dt": float(dt[i])} for i in gi if tr.alt.iloc[i] > 0]
    route = None
    if plan:
        route = {"names": plan["names"], "levels": plan["levels"], "rfl": plan["rfl"], "type": plan["type"],
                 "route": plan["route"], "changes": plan["changes"], "blocking": plan["blocking"], "edge": plan["edge"],
                 "source": plan["source"], "usable": plan["usable"],
                 "coords": [[project.adep_lat if f.adep == project.adep else project.ades_lat,
                             project.adep_lon if f.adep == project.adep else project.ades_lon]] +
                           [list(fixes[n]) for n in plan["names"] if n in fixes] +
                           [[project.ades_lat if f.ades == project.ades else project.adep_lat,
                             project.ades_lon if f.ades == project.ades else project.adep_lon]],
                 "fix_points": [{"name": n, "lat": fixes[n][0], "lon": fixes[n][1], "level": l} for n, l in
                                zip(plan["names"], plan["levels"]) if n in fixes]}
    tabs = {}
    for key in ("levels", "changes", "waypoints"):
        p = rd / f"{key}.parquet"
        if p.exists():
            t = pd.read_parquet(p)
            tabs[key] = [{k: _clean(v) for k, v in rr.items()} for rr in t[t.fid == fid].to_dict("records")] if not t.empty else []
    xp = rd / "xt.parquet"
    if xp.exists():
        xt = pads.dataset(xp).to_table(filter=pads.field("fid") == fid).to_pandas()
        tabs["xt"] = {"lat": xt.lat.round(3).tolist(), "lon": xt.lon.round(3).tolist(), "xt": xt.xt.round(2).tolist()}
    # group medians for the metric card
    grp = res[(res.dirn == r.dirn) & (res["type"] == r["type"])]
    card_cols = [c for c in res.columns if c.startswith(("ext_", "extp_", "time_", "flown_", "ref_", "arr_time_", "arr_dist_",
                                                             "dep_time_")) or c in (
        "toc_min", "toc_dist", "tod_dist", "tod_to_end_min", "cruise_changes", "score_total", "score_above", "score_below",
        "time_above_min", "time_below_min", "time_at_min", "net_turn", "xt_median", "xt_max", "airborne_min", "flown_total")]
    card = [{"metric": c, "value": _clean(r.get(c)), "group_median": _clean(grp[c].median()) if c in grp and grp[c].notna().any() else None}
            for c in card_cols]
    meta = {k: _clean(r.get(k)) for k in ("fid", "callsign", "dof", "adep", "ades", "dirn", "type", "rfl", "cluster", "outlier",
                                           "contingency", "holding", "capped_climb", "has_plan", "has_ref", "toc_rule",
                                           "first_cruise", "first_vs_rfl", "max_alt")}
    meta["flags"] = f.flags
    meta["plan_raw"] = None
    from ..models import FlightPlanRecord
    recs = db.query(FlightPlanRecord).filter_by(project_id=project.id, callsign=f.callsign, dof=f.dof).order_by(FlightPlanRecord.filed_time).all()
    meta["plan_messages"] = [{"source": x.source, "msg_type": x.msg_type, "filed_time": x.filed_time, "raw": x.raw,
                              "flags": (x.parsed or {}).get("api_flags", []), "atd": x.atd, "ata": x.ata, "status": x.status} for x in recs]
    return {"meta": meta, "track": track, "profile": prof, "route": route, "gaps": gaps,
            "level_segments": [{"t_min": round((rs["t"][a] - t0) / 60, 2), "dur_s": (b - a) * step,
                                "alt": float(np.median(rs["alt"][a:b]))} for a, b in segs],
            "toc_t_min": _clean(r.get("toc_min")),
            "tod_t_min": _clean((rs["t"][-1] - t0) / 60 - r.get("tod_to_end_min")) if pd.notna(r.get("tod_to_end_min")) else None,
            "card": card, **tabs,
            "airports": {project.adep: [project.adep_lat, project.adep_lon], project.ades: [project.ades_lat, project.ades_lon]},
            "rings": [float(x) for x in params["HOR-01b"]]}
