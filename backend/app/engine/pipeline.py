"""Analysis run orchestration (SRS 5.6). Called in a background thread."""
import json
import re
import traceback
from collections import defaultdict

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from .. import config
from ..db import SessionLocal
from ..models import Airspace, Airway, Dataset, Fix, Flight, FlightPlanRecord, Insight, ParameterSet, Project, Run, now
from . import aggregate, insights
from .analysis import Ctx, analyse_flight, load_polygons
from .fpl import expand_route, planned_profile, select_version
from .geo import dist_fn
from .tracks import evaluate_inclusion


def load_navdb(db):
    """Fixes: NavEarth (AIP Indonesia) wins over imported data for the same name."""
    fixes, src = {}, {}
    rank = {"navearth": 3, "manual": 2, "import": 1}
    for f in db.query(Fix).all():
        if f.name not in fixes or rank.get(f.source, 0) > rank.get(src[f.name], 0):
            fixes[f.name] = (f.lat, f.lon)
            src[f.name] = f.source
    # Airways: every source is kept, best source first. NavEarth only covers Indonesian airspace, so its version
    # of an international airway (e.g. N875 from AKDAT) must not hide the full one from the imported data.
    from .ingest import split_chains
    rows = sorted(db.query(Airway).all(), key=lambda a: -rank.get(a.source, 0))
    airways = {}
    for a in rows:
        airways.setdefault(a.name, []).extend(split_chains(a.fixes))
    return fixes, airways


def apply_chg(parsed: dict, chg_raw_items: list) -> dict:
    """Apply field 15 / field 9 amendments of a CHG message (simplified)."""
    out = dict(parsed)
    for it in chg_raw_items:
        m = re.match(r"^(\d{1,2})/(.*)$", it.strip())
        if not m:
            continue
        field, val = m.group(1), m.group(2).strip()
        if field == "15":
            from .fpl import parse_message
            fake = f"(FPL-{out.get('callsign','X')}-IS\n-{out.get('type','')}/{out.get('wake','')}-S/C\n-{out.get('adep','')}{out.get('eobt','')}\n-{val}\n-{out.get('ades','')}{out.get('eet','')}\n-{out.get('item18','')})"
            p2 = parse_message(fake)
            for k in ("speed", "rfl", "route", "changes"):
                out[k] = p2.get(k, out.get(k))
        elif field == "9":
            out["type"] = val.split("/")[0]
    return out


def build_plans(db, project_id: int, flights: pd.DataFrame, params: dict, fixes: dict, airways: dict):
    """One effective plan per flight: version rule (FPL-01), CHG applied, Item 15 expanded."""
    recs = db.query(FlightPlanRecord).filter(FlightPlanRecord.project_id == project_id).all()
    src_mode = params["FPL-03"]
    by = defaultdict(list)
    for r in recs:
        if src_mode == "file" and r.source != "file":
            continue
        if src_mode == "api" and r.source != "api":
            continue
        by[(r.callsign, r.dof)].append(r)
    plans, unresolved = {}, defaultdict(int)
    for _, f in flights.iterrows():
        rs = by.get((f.callsign, f.dof), [])
        if src_mode == "api_with_file_fallback" and any(r.source == "api" for r in rs):
            rs = [r for r in rs if r.source == "api"]
        # keep only records whose aerodromes match the flight direction
        rs = [r for r in rs if not r.parsed.get("adep") or (r.parsed.get("adep") == f.adep and r.parsed.get("ades") == f.ades)
              or r.parsed.get("msg_type") != "FPL"]
        if not rs:
            continue
        items = [{"filed_iso": r.filed_time, "parsed": r.parsed, "rec": r} for r in rs]
        sel = select_version(items, params["FPL-01"], f.dof)
        if not sel:
            continue
        parsed = dict(sel["parsed"])
        for r in sorted(rs, key=lambda r: r.filed_time or ""):
            if r.parsed.get("msg_type") == "CHG" and (r.filed_time or "") >= (sel["filed_iso"] or ""):
                parsed = apply_chg(parsed, r.parsed.get("items", []))
            if r.parsed.get("msg_type") == "CNL" and (r.filed_time or "") >= (sel["filed_iso"] or ""):
                parsed["cancelled"] = True
        names, blocking, edge = expand_route(parsed.get("route", ""), fixes, airways)
        for u in blocking:
            unresolved[u] += 1
        for u in edge:
            unresolved[f"{u} (route end)"] += 1
        usable = bool(names) and (not blocking or params["FPL-02"] == "straight_line")
        levels = planned_profile(names, parsed.get("rfl"), parsed.get("changes", []))
        coords = {n: fixes[n] for n in names if n in fixes}
        plans[f.fid] = {"names": names, "levels": levels, "rfl": parsed.get("rfl"), "changes": parsed.get("changes", []),
                        "coords": coords, "usable": usable, "type": parsed.get("type", ""), "route": parsed.get("route", ""),
                        "blocking": sorted(blocking), "edge": sorted(edge), "source": sel["rec"].source,
                        "cancelled": parsed.get("cancelled", False), "n_versions": len([i for i in items if i["parsed"].get("msg_type") == "FPL"])}
    return plans, dict(unresolved)


def classify(res: pd.DataFrame, params: dict) -> pd.DataFrame:
    """FR-ANA-10..14: k-means on cut-line crossings per direction, outliers and contingency clusters."""
    k = int(params["CLU-03"])
    res["cluster"] = -1
    res["outlier"] = False
    res["contingency"] = False
    cuts = [float(c) for c in params["CLU-02"]]
    for dirn, g in res.groupby("dirn"):
        X = np.array([c if isinstance(c, (list, np.ndarray)) and len(c) == len(cuts) else [np.nan] * len(cuts)
                      for c in g["cross"]], float)
        if len(X) == 0:
            continue
        miss = np.isnan(X)
        col_med = np.nanmedian(X, axis=0)
        col_med = np.where(np.isnan(col_med), 0, col_med)
        Xf = np.where(miss, col_med, X)
        kk = max(1, min(k, len(Xf)))
        km = KMeans(n_clusters=kk, n_init=10, random_state=0).fit(Xf)
        lab = km.labels_
        res.loc[g.index, "cluster"] = lab
        cen = km.cluster_centers_[lab]
        dev = np.abs(Xf - cen)
        out = np.zeros(len(Xf), bool)
        for c in range(kk):
            m = lab == c
            mad = np.median(dev[m], axis=0) * 1.4826
            lim = np.maximum(float(params["CLU-05"]) * mad, float(params.get("CLU-05b", 1.5)))
            out[m] = (dev[m] > lim).any(axis=1)
        out |= miss.sum(1) > 1
        res.loc[g.index, "outlier"] = out
        share = pd.Series(lab).value_counts(normalize=True) * 100
        small = set(share[share < float(params["CLU-04"])].index)
        res.loc[g.index, "contingency"] = [l in small for l in lab]
        res.loc[g.index, "centroid"] = pd.Series([json.dumps(list(map(float, c))) for c in km.cluster_centers_[lab]],
                                                 index=g.index)
    return res


def crosstab_k(res: pd.DataFrame, params: dict) -> list:
    """FR-ANA-12: cluster vs filed route for k = 1..5 to help choose k."""
    out = []
    cuts = len(params["CLU-02"])
    for dirn, g in res.groupby("dirn"):
        X = np.array([c if isinstance(c, (list, np.ndarray)) and len(c) == cuts else [np.nan] * cuts for c in g["cross"]], float)
        X = np.where(np.isnan(X), np.nanmedian(X, axis=0), X)
        X = np.nan_to_num(X)
        routes = g["route_key"].fillna("(no plan)").values
        for k in range(1, min(5, len(X)) + 1):
            lab = KMeans(n_clusters=k, n_init=5, random_state=0).fit(X).labels_
            tab = pd.crosstab(lab, routes)
            purity = float(tab.max(axis=1).sum() / tab.values.sum())
            top = {int(c): tab.loc[c].sort_values(ascending=False).head(3).to_dict() for c in tab.index}
            out.append({"dirn": dirn, "k": k, "purity": purity, "clusters": {str(c): {r: int(n) for r, n in v.items()} for c, v in top.items()},
                        "sizes": [int(x) for x in np.bincount(lab)]})
    return out


def execute(run_id: int):
    db = SessionLocal()
    run = db.get(Run, run_id)
    try:
        run.status, run.started_at, run.software_version = "running", now(), config.VERSION
        db.commit()

        def progress(frac, step, msg=""):
            run.progress, run.step, run.message = float(frac), step, msg
            db.commit()

        project = db.get(Project, run.project_id)
        ds = db.get(Dataset, run.dataset_id)
        params = db.get(ParameterSet, run.paramset_id).values
        ctx = Ctx(params, project, dist_fn(params["GEN-01"]))
        progress(0.02, "load", "Loading tracks")
        pts = pd.read_parquet(config.dataset_dir(ds.id) / "points.parquet")
        frows = db.query(Flight).filter(Flight.dataset_id == ds.id).all()
        flights = pd.DataFrame([{"fid": f.fid, "callsign": f.callsign, "dof": f.dof, "adep": f.adep, "ades": f.ades,
                                 "duration_min": f.duration_min, "obj": f} for f in frows])
        incl = [evaluate_inclusion(f, params) for f in flights.obj]
        flights["included"] = [i[0] for i in incl]
        flights["exclusion"] = [i[1] for i in incl]
        progress(0.05, "fpl", "Building flight plans")
        fixes, airways = load_navdb(db)
        plans, unresolved = build_plans(db, project.id, flights, params, fixes, airways)
        groups = dict(tuple(pts.groupby("fid", sort=False)))
        sink = {"xt": [], "levels": [], "changes": [], "waypoints": [], "fir": []}
        polys = load_polygons(db.query(Airspace).filter(Airspace.kind != "tma").all())
        rows = []
        inc = flights[flights.included]
        n = len(inc)
        for i, f in enumerate(inc.itertuples()):
            tr = groups.get(f.fid)
            if tr is None or len(tr) < 10:
                continue
            tr = tr.sort_values("t").reset_index(drop=True)
            plan = plans.get(f.fid)
            r = analyse_flight(f.fid, tr, f.adep, f.ades, plan, fixes, ctx, sink, polys)
            r.update(callsign=f.callsign, dof=f.dof, adep=f.adep, ades=f.ades, dirn=f"{f.adep}-{f.ades}",
                     month=f.dof[:7], type=(plan or {}).get("type", "") or "unknown",
                     has_plan=plan is not None, plan_usable=bool(plan and plan.get("usable")),
                     route_key=" ".join(t for t in (plan or {}).get("route", "").split() if "/" not in t) if plan else None,
                     rfl=(plan or {}).get("rfl"), plan_source=(plan or {}).get("source", ""),
                     n_fpl_versions=(plan or {}).get("n_versions", 0))
            rows.append(r)
            if i % 25 == 0:
                progress(0.05 + 0.75 * i / max(1, n), "metrics", f"Flight {i + 1} of {n}")
        res = pd.DataFrame(rows)
        progress(0.82, "classify", "Classifying routes")
        res = classify(res, params)
        try:
            ctab = crosstab_k(res, params)
        except Exception:  # noqa: BLE001
            ctab = []
        rd = config.run_dir(run.id)
        res["cross"] = res["cross"].apply(lambda c: json.dumps([None if (x is None or (isinstance(x, float) and np.isnan(x))) else x for x in c]) if isinstance(c, list) else "[]")
        res.to_parquet(rd / "flights.parquet")
        for key in ("levels", "changes", "waypoints", "fir"):
            df = pd.DataFrame(sink[key])
            df.to_parquet(rd / f"{key}.parquet")
        xt = pd.concat(sink["xt"]) if sink["xt"] else pd.DataFrame(columns=["fid", "lat", "lon", "xt"])
        xt.to_parquet(rd / "xt.parquet")
        excl = flights[~flights.included][["fid", "callsign", "dof", "exclusion"]].to_dict("records")
        meta = {"unresolved_fixes": unresolved, "crosstab": ctab, "excluded": excl,
                "n_loaded": int(len(flights)), "n_included": int(flights.included.sum()),
                "n_with_plan": int(res.has_plan.sum()) if len(res) else 0,
                "n_ref": int(res.has_ref.sum()) if len(res) else 0, "earth_model": params["GEN-01"]}
        (rd / "meta.json").write_text(json.dumps(meta, default=str))
        progress(0.9, "aggregate", "Aggregating")
        summary = aggregate.compute(rd, params, project, filters={}, quality=ds.quality)
        (rd / "summary.json").write_text(json.dumps(summary, default=float))
        progress(0.95, "insights", "Drafting insights")
        db.query(Insight).filter(Insight.run_id == run.id).delete()
        for ins in insights.evaluate(summary, params, project):
            db.add(Insight(run_id=run.id, **ins))
        run.summary = {"kpis": summary.get("kpis", []), "n": summary.get("n", {})}
        run.status, run.progress, run.step, run.message, run.finished_at = "done", 1.0, "done", "Completed", now()
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        run = db.get(Run, run_id)
        run.status, run.message, run.finished_at = "error", f"{e}\n{traceback.format_exc()[-1500:]}", now()
        db.commit()
    finally:
        db.close()
