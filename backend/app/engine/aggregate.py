"""Aggregation of per-flight results into the Accumulated view and report data (FR-ANA-61, FR-VIS-30..42).

compute() is called after a run and again whenever the user changes filters.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _q(x, q):
    x = pd.Series(x).dropna()
    return float(x.quantile(q)) if len(x) else None


def _f(x):
    if x is None:
        return None
    try:
        if np.isnan(x):
            return None
    except TypeError:
        pass
    return float(x)


def load(rd: Path):
    res = pd.read_parquet(rd / "flights.parquet")
    tabs = {k: (pd.read_parquet(rd / f"{k}.parquet") if (rd / f"{k}.parquet").exists() else pd.DataFrame())
            for k in ("levels", "changes", "waypoints", "xt", "fir")}
    meta = json.loads((rd / "meta.json").read_text()) if (rd / "meta.json").exists() else {}
    return res, tabs, meta


def apply_filters(res: pd.DataFrame, f: dict) -> pd.DataFrame:
    m = pd.Series(True, index=res.index)
    if f.get("dirn"):
        m &= res.dirn.isin(f["dirn"] if isinstance(f["dirn"], list) else [f["dirn"]])
    if f.get("callsign"):
        m &= res.callsign.isin(f["callsign"] if isinstance(f["callsign"], list) else [f["callsign"]])
    if f.get("type"):
        m &= res["type"].isin(f["type"] if isinstance(f["type"], list) else [f["type"]])
    if f.get("cluster") not in (None, "", "all"):
        m &= res.cluster.astype(str) == str(f["cluster"])
    if f.get("month_from"):
        m &= res.month >= f["month_from"]
    if f.get("month_to"):
        m &= res.month <= f["month_to"]
    if f.get("season"):
        sm = {"DJF": ["12", "01", "02"], "MAM": ["03", "04", "05"], "JJA": ["06", "07", "08"], "SON": ["09", "10", "11"]}
        m &= res.month.str[5:7].isin(sm.get(f["season"], []))
    return res[m]


def type_group(t: str) -> str:
    return t or "unknown"


def level_bounds(params, phase):
    if phase == "climb":
        mode = params["VER-07"]
        if mode == "caas":
            return 0, 33000
        if mode == "custom":
            return tuple(params["VER-07c"][:2])
        return 11000, 1e9
    mode = params["VER-08"]
    if mode == "caas":
        return 7500, 33000
    if mode == "custom":
        return tuple(params["VER-08c"][:2])
    return 11000, 1e9


def counted_levels(levels: pd.DataFrame, params, min_dur=None) -> pd.DataFrame:
    """Level segments that count as level-offs under the configured bounds and exclusions."""
    if levels.empty:
        return levels
    min_dur = float(params["VER-03"]) if min_dur is None else min_dur
    L = levels[levels.dur >= min_dur]
    keep = pd.Series(False, index=L.index)
    for ph in ("climb", "descent"):
        lo, hi = level_bounds(params, ph)
        keep |= (L.phase == ph) & (L.alt >= lo) & (L.alt <= hi)
    ia, idist = [float(x) for x in params["VER-10"]][:2]
    if ia > 0:
        keep &= ~((L.phase == "descent") & (L.alt < ia) & (L.d_ades < idist))
    return L[keep]


def compute(rd: Path, params: dict, project, filters: dict, quality: dict | None = None) -> dict:
    res_all, tabs, meta = load(rd)
    res = apply_filters(res_all, filters or {})
    fids = set(res.fid)
    lev = tabs["levels"][tabs["levels"].fid.isin(fids)] if not tabs["levels"].empty else tabs["levels"]
    chg = tabs["changes"][tabs["changes"].fid.isin(fids)] if not tabs["changes"].empty else tabs["changes"]
    wps = tabs["waypoints"][tabs["waypoints"].fid.isin(fids)] if not tabs["waypoints"].empty else tabs["waypoints"]
    xt = tabs["xt"][tabs["xt"].fid.isin(fids)] if not tabs["xt"].empty else tabs["xt"]
    main = int(float(params["HOR-01"]))
    rings = sorted(set(int(float(x)) for x in list(params["HOR-01b"]) + [params["HOR-01"]]))
    plo, phi = [float(x) / 100 for x in params["HOR-02"]][:2]
    clean = res[~res.outlier & ~res.contingency] if "outlier" in res else res
    S = {"filters": filters or {}, "main_ring": main, "rings": rings, "earth_model": params["GEN-01"]}
    dirs = sorted(res.dirn.unique().tolist())
    S["dirs"] = dirs
    S["options"] = {"dirn": sorted(res_all.dirn.unique().tolist()), "callsign": sorted(res_all.callsign.unique().tolist()),
                    "type": sorted(res_all["type"].unique().tolist()), "months": sorted(res_all.month.unique().tolist()),
                    "cluster": sorted(res_all.cluster.astype(int).unique().tolist()) if "cluster" in res_all else []}
    S["n"] = {"flights": int(len(res)), "with_plan": int(res.has_plan.sum()), "with_ref": int(res.has_ref.sum()),
              "outliers": int(res.outlier.sum()) if "outlier" in res else 0,
              "contingency": int(res.contingency.sum()) if "contingency" in res else 0,
              "by_dirn": res.groupby("dirn").size().to_dict(),
              "loaded": meta.get("n_loaded"), "included": meta.get("n_included")}
    S["meta"] = {k: meta.get(k) for k in ("unresolved_fixes", "crosstab", "excluded")}

    # ---- clusters (metric 7)
    cl = []
    if "cluster" in res:
        for (d, c), g in res.groupby(["dirn", "cluster"]):
            cl.append({"dirn": d, "cluster": int(c), "n": int(len(g)), "share": float(len(g) / max(1, (res.dirn == d).sum())),
                       "contingency": bool(g.contingency.any()), "outliers": int(g.outlier.sum()),
                       "centroid": json.loads(g.centroid.iloc[0]) if "centroid" in g and isinstance(g.centroid.iloc[0], str) else None})
        monthly = res.groupby(["dirn", "cluster", "month"]).size().reset_index(name="n")
        tot = res.groupby(["dirn", "month"]).size().rename("N")
        monthly = monthly.join(tot, on=["dirn", "month"])
        monthly["share"] = monthly.n / monthly.N
        S["clusters"] = {"summary": cl, "monthly": monthly.assign(cluster=monthly.cluster.astype(int)).to_dict("records"),
                         "cuts": params["CLU-02"], "orientation": params["CLU-02b"],
                         "crossings": [{"fid": r.fid, "dirn": r.dirn, "cluster": int(r.cluster), "outlier": bool(r.outlier),
                                        "x": json.loads(r.cross) if isinstance(r.cross, str) else []} for r in res.itertuples()]}

    # ---- horizontal (metrics 1-5)
    ext = []
    for R in rings:
        for d in dirs:
            g = clean[clean.dirn == d]
            col = f"ext_{R}"
            if col not in g:
                continue
            x = g[col].dropna()
            if not len(x):
                continue
            ext.append({"ring": R, "dirn": d, "n": int(len(x)), "median": _q(x, .5), "p15": _q(x, plo), "p85": _q(x, phi),
                        "shorter_share": float((x < 0).mean()), "median_pct": _q(g[f"extp_{R}"], .5),
                        "ref_median": _q(g.get(f"ref_{R}"), .5), "flown_median": _q(g.get(f"flown_{R}"), .5),
                        "ext_gc_median": _q(g.get(f"ext_gc_{R}"), .5), "hfe_gc_pct": _q(100 * g[f"ext_gc_{R}"] / g[f"gcd_{R}"], .5) if f"gcd_{R}" in g else None,
                        "time_median": _q(g.get(f"time_{R}"), .5),
                        "time_var": (_q(g.get(f"time_{R}"), phi) - _q(g.get(f"time_{R}"), plo)) / 2 if g.get(f"time_{R}") is not None and g[f"time_{R}"].notna().any() else None})
    S["extension"] = ext
    col = f"ext_{main}"
    S["extension_box"] = []
    for keys in (["dirn"], ["dirn", "type"], ["dirn", "month"]):
        if col not in clean:
            break
        for k, g in clean.groupby(keys):
            x = g[col].dropna()
            if len(x) < 3:
                continue
            k = k if isinstance(k, tuple) else (k,)
            S["extension_box"].append({"by": "+".join(keys), "key": " · ".join(map(str, k)), "n": int(len(x)),
                                       "min": _q(x, 0.05), "p25": _q(x, .25), "median": _q(x, .5), "p75": _q(x, .75), "max": _q(x, .95)})
    tcol = f"time_{main}"
    tm = []
    if tcol in clean:
        for (d, m), g in clean.groupby(["dirn", "month"]):
            x = g[tcol].dropna()
            if len(x):
                tm.append({"dirn": d, "month": m, "n": int(len(x)), "median": _q(x, .5), "p15": _q(x, plo), "p85": _q(x, phi),
                           "var": (_q(x, phi) - _q(x, plo)) / 2})
    S["enroute_monthly"] = tm
    var = []
    if tcol in clean:
        for keys in (["dirn"], ["dirn", "callsign"], ["dirn", "type"], ["dirn", "cluster"]):
            for k, g in clean.groupby(keys):
                x = g[tcol].dropna()
                if len(x) < 5:
                    continue
                k = k if isinstance(k, tuple) else (k,)
                var.append({"by": "+".join(keys), "key": " · ".join(map(str, k)), "n": int(len(x)), "median": _q(x, .5),
                            "var": (_q(x, phi) - _q(x, plo)) / 2})
    S["variance"] = var
    # lateral profile
    lat = []
    if not xt.empty:
        b = float(params["HOR-04"])
        x2 = xt.merge(res[["fid", "dirn"]], on="fid")
        x2["bin"] = (np.floor(x2.lat / b) * b + b / 2).round(3)
        for (d, bb), g in x2.groupby(["dirn", "bin"]):
            if len(g) < 5:
                continue
            lat.append({"dirn": d, "lat": float(bb), "median": _q(g.xt, .5), "p90": _q(g.xt, .9), "n": int(g.fid.nunique())})
    S["lateral"] = lat
    S["fir"] = []
    fr = tabs["fir"][tabs["fir"].fid.isin(fids)] if not tabs["fir"].empty else tabs["fir"]
    if not fr.empty:
        f2 = fr.merge(res[["fid", "dirn"]], on="fid")
        for (d, c), g in f2.groupby(["dirn", "fir"]):
            S["fir"].append({"dirn": d, "fir": c, "ansp": g.ansp.iloc[0], "n": int(g.fid.nunique()),
                             "flown_median": _q(g.flown, .5), "ref_median": _q(g.ref, .5),
                             "ext_median": _q(g.flown - g.ref, .5), "time_median": _q(g.time, .5)})
    S["coverage"] = (quality or {}).get("coverage")

    # ---- terminal
    rings_t = [int(float(x)) for x in params["TER-01"]]
    big = max(rings_t) if rings_t else 100
    ter, tmon, scat, corr = [], [], [], {}
    up = float(params["TER-02"]) / 100
    for ap, g in res.groupby("ades"):
        for R in rings_t:
            c = f"arr_time_{R}"
            if c not in g:
                continue
            x = g[c].dropna()
            if not len(x):
                continue
            u = _q(x, up)
            ter.append({"ades": ap, "ring": R, "n": int(len(x)), "median": _q(x, .5), "p85": _q(x, .85),
                        "var": (_q(x, phi) - _q(x, plo)) / 2, "unimpeded": u,
                        "add_mean": float((x - u).clip(lower=0).mean()), "dist_median": _q(g.get(f"arr_dist_{R}"), .5),
                        "dist_p85": _q(g.get(f"arr_dist_{R}"), .85),
                        "hold_share": float(g.holding.mean()) if "holding" in g else None})
        c = f"arr_time_{big}"
        if c in g:
            u = _q(g[c], up)
            for m, gm in g.groupby("month"):
                tmon.append({"ades": ap, "month": m, "n": int(len(gm)), "add_mean": float((gm[c] - u).clip(lower=0).mean()),
                             "hold_share": float(gm.holding.mean())})
            if tcol in g:
                gg = g[[tcol, c, "fid"]].dropna()
                corr[ap] = _f(gg[tcol].corr(gg[c])) if len(gg) > 5 else None
                for r in gg.sample(min(len(gg), 600), random_state=0).itertuples():
                    scat.append({"ades": ap, "fid": r.fid, "enroute": float(getattr(r, tcol)), "arrival": float(getattr(r, c))})
    S["terminal"] = {"by_ades": ter, "monthly": tmon, "scatter": scat, "corr": corr, "big_ring": big,
                     "dep": [{"adep": ap, "ring": R, "median": _q(g.get(f"dep_time_{R}"), .5)} for ap, g in res.groupby("adep") for R in rings_t if f"dep_time_{R}" in g]}

    # ---- cruise (metric 8 + waypoint check)
    cr = {"classes": [], "score": [], "first_vs_rfl": [], "capped": [], "changes": {}}
    if not wps.empty:
        w = wps.merge(res[["fid", "dirn", "type"]], on="fid")
        for (d, t), g in w.groupby(["dirn", "type"]):
            sh = g.cls.value_counts(normalize=True)
            per_f = g.groupby("fid").dev.apply(lambda x: (x.abs() <= float(params["CRU-05"])).all())
            cr["classes"].append({"dirn": d, "type": t, "n_wp": int(len(g)), "n_flights": int(g.fid.nunique()),
                                  **{k: float(sh.get(k, 0)) for k in ["below_2000", "below_1000", "at", "above_1000", "above_2000"]},
                                  "flights_all_at": float(per_f.mean()),
                                  "window_spans_change": float(((g.wmax - g.wmin) >= 900).mean())})
    if "score_total" in res:
        for (d, t), g in res.groupby(["dirn", "type"]):
            x = g.score_total.dropna()
            if not len(x):
                continue
            cr["score"].append({"dirn": d, "type": t, "n": int(len(x)), "median": _q(x, .5), "p85": _q(x, .85),
                                "share_above": _q(g.get("share_above"), .5), "share_below": _q(g.get("share_below"), .5),
                                "time_above_mean": float(g.time_above_min.mean()), "time_below_mean": float(g.time_below_min.mean()),
                                "time_at_mean": float(g.time_at_min.mean())})
    if "first_vs_rfl" in res:
        for (d, t), g in res.groupby(["dirn", "type"]):
            x = g.first_vs_rfl.dropna()
            if len(x):
                cr["first_vs_rfl"].append({"dirn": d, "type": t, "n": int(len(x)), "equal_share": float((x == 0).mean()),
                                           "dist": {str(int(k)): float(v) for k, v in x.clip(-4000, 4000).value_counts(normalize=True).sort_index().items()}})
    if "capped_climb" in res:
        for (d, t), g in res.groupby(["dirn", "type"]):
            if g.rfl.notna().sum() == 0:
                continue
            cc = g[g.capped_climb == True]  # noqa: E712
            cr["capped"].append({"dirn": d, "type": t, "n": int(g.rfl.notna().sum()), "share": float(len(cc) / max(1, g.rfl.notna().sum())),
                                 "median_below_ft": _q(cc.rfl - cc.capped_level, .5) if len(cc) else None,
                                 "median_dist": _q(cc.capped_dist, .5) if len(cc) else None,
                                 "median_min": _q(cc.capped_min, .5) if len(cc) else None})
    if not chg.empty:
        reach = chg[chg.reaches_planned]
        cr["changes"] = {"n": int(len(chg)), "climb_share": float((chg.kind == "climb").mean()),
                         "n_reach_planned": int(len(reach)),
                         "matched_share": float(reach.matched.mean()) if len(reach) else None,
                         "median_dist_planned": _q(reach.dist_planned, .5) if len(reach) else None,
                         "on_unplanned_share": float((chg.n_planned == 0).mean()),
                         "transition_median_min": _q(chg.transition_min, .5),
                         "points": chg.sample(min(len(chg), 800), random_state=0)[["lat", "lon", "kind", "matched", "reaches_planned"]].to_dict("records"),
                         "by_lat": chg.assign(b=np.floor(chg.lat)).groupby(["b", "kind"]).size().reset_index(name="n").to_dict("records")}
    S["cruise"] = cr

    # ---- level-offs (metrics 9, 10)
    lo_ = {"sensitivity": [], "hist": [], "bands": [], "primary": []}
    if not lev.empty:
        L = lev.merge(res[["fid", "adep", "ades", "type"]], on="fid")
        L["airport"] = np.where(L.phase == "climb", L.adep, L.ades)
        nflt = {}
        for ap in set(res.adep) | set(res.ades):
            nflt[("climb", ap)] = int((res.adep == ap).sum())
            nflt[("descent", ap)] = int((res.ades == ap).sum())
        durs = sorted(set(float(x) for x in params["VER-03b"]))
        for (ph, ap), g in L.groupby(["phase", "airport"]):
            N = max(1, nflt.get((ph, ap), 1))
            lo, hi = level_bounds(params, ph)
            for dd in durs:
                c = counted_levels(g, params, dd)
                lo_["sensitivity"].append({"phase": ph, "airport": ap, "dur": dd, "share": float(c.fid.nunique() / N)})
            c = counted_levels(g, params)
            lo_["primary"].append({"phase": ph, "airport": ap, "flights": N, "share": float(c.fid.nunique() / N),
                                   "mean_per_flight": float(len(c) / N), "median_dur": _q(c.dur, .5),
                                   "time_per_flight_min": float(c.dur.sum() / 60 / N), "bounds": [lo, hi if hi < 1e8 else None]})
            g60 = g[g.dur >= 60]
            h = np.histogram(g60.alt, bins=np.arange(0, 46000, 1000))[0]
            lo_["hist"].append({"phase": ph, "airport": ap, "edges": list(range(0, 45000, 1000)), "counts": h.tolist()})
            edges = [float(x) for x in params["VER-09"]]
            for a, b in zip(edges[:-1], edges[1:]):
                gb = g60[(g60.alt >= a) & (g60.alt < b)]
                lo_["bands"].append({"phase": ph, "airport": ap, "band": f"{int(a):,}–{int(b):,} ft", "flights": int(gb.fid.nunique()),
                                     "segments": int(len(gb)), "median_dur": _q(gb.dur, .5), "median_dist": _q(gb.d_ades if ph == "descent" else gb.d_adep, .5),
                                     "min_per_flight": float(gb.dur.sum() / 60 / N)})
    S["leveloffs"] = lo_
    S["vertical"] = {"toc_min": _q(res.get("toc_min"), .5), "toc_dist": _q(res.get("toc_dist"), .5),
                     "tod_dist": _q(res.get("tod_dist"), .5), "tod_to_end_min": _q(res.get("tod_to_end_min"), .5),
                     "by_dirn": [{"dirn": d, "toc_dist": _q(g.get("toc_dist"), .5), "tod_dist": _q(g.get("tod_dist"), .5),
                                  "changes_mean": _f(g.cruise_changes.mean()) if "cruise_changes" in g else None} for d, g in res.groupby("dirn")]}
    S["types"] = res.groupby(["callsign", "type"]).size().reset_index(name="n").to_dict("records")
    S["kpis"] = kpis(S, res)
    return S


def kpis(S, res):
    k = []
    main = S["main_ring"]
    k.append({"id": "flights", "label": "Flights analysed", "value": S["n"]["flights"], "unit": "", "chart": "coverage"})
    for e in S["extension"]:
        if e["ring"] == main:
            k.append({"id": f"ext_{e['dirn']}", "label": f"Path extension vs filed, {e['dirn']}", "value": round(e["median"], 1),
                      "unit": "NM (median)", "chart": "extension"})
    for t in S["terminal"]["by_ades"]:
        if t["ring"] == S["terminal"]["big_ring"]:
            k.append({"id": f"hold_{t['ades']}", "label": f"Holding-type arrivals, {t['ades']}",
                      "value": round(100 * (t["hold_share"] or 0), 1), "unit": "%", "chart": "terminal"})
            k.append({"id": f"var_{t['ades']}", "label": f"Arrival time variance (last {t['ring']} NM), {t['ades']}",
                      "value": round(t["var"], 1), "unit": "min", "chart": "terminal"})
    for p in S["leveloffs"]["primary"]:
        if p["phase"] == "descent":
            k.append({"id": f"lo_{p['airport']}", "label": f"Descent level-offs into {p['airport']}",
                      "value": round(100 * p["share"], 1), "unit": "% of flights", "chart": "leveloffs"})
    return k
