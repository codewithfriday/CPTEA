"""Per-flight computation of every metric (SRS 5.3-5.5)."""
import numpy as np
import pandas as pd

from .geo import bearing, crossing, cumulative, densify, gc, path_length, ring_window
from .tracks import resample, segments


class Ctx:
    """Everything a flight needs: parameters, aerodromes, distance function."""

    def __init__(self, params: dict, project, dist):
        self.p = params
        self.dist = dist
        self.ap = {project.adep: (project.adep_lat, project.adep_lon),
                   project.ades: (project.ades_lat, project.ades_lon)}

    def rings(self):
        r = sorted(set([float(self.p["HOR-01"])] + [float(x) for x in self.p["HOR-01b"]]))
        return r


def plan_geometry(plan: dict | None, adep: str, ades: str, ctx: Ctx, fixes: dict):
    """Reference polyline (ADEP + expanded fixes + ADES), its densified form and planned levels per fix."""
    if not plan or not plan.get("names"):
        return None
    names = [n for n in plan["names"] if n in fixes and n not in (adep, ades)]
    if not names:
        return None
    pts = [ctx.ap[adep]] + [tuple(fixes[n]) for n in names] + [ctx.ap[ades]]
    rl, ro = densify(pts, 1.0)
    # along-route distance of each fix
    cum_pts = cumulative(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]), ctx.dist)
    levels = plan.get("levels") or [plan.get("rfl")] * len(plan["names"])
    lvl_map = dict(zip(plan["names"], levels))
    fix_levels = [lvl_map.get(n, plan.get("rfl")) for n in names]
    return {"names": names, "pts": pts, "rl": rl, "ro": ro, "rcum": cumulative(rl, ro, ctx.dist),
            "fix_cum": cum_pts[1:-1], "fix_levels": fix_levels, "rfl": plan.get("rfl")}


def net_turn(la, lo, seg_min=0.3):
    if len(la) < 4:
        return 0.0
    seg = gc(la[:-1], lo[:-1], la[1:], lo[1:])
    m = seg > seg_min
    if m.sum() < 3:
        return 0.0
    b = bearing(la[:-1][m], lo[:-1][m], la[1:][m], lo[1:][m])
    db = (np.diff(b) + 180) % 360 - 180
    return float(np.abs(np.cumsum(db)).max())


def horizontal(tr, adep, ades, geom, ctx: Ctx, out: dict, xt_rows: list, fid: str):
    p = ctx.p
    la, lo, t = tr.lat.values, tr.lon.values, tr.t.values.astype(float)
    A, B = ctx.ap[adep], ctx.ap[ades]
    for R in ctx.rings():
        k = int(R)
        w = ring_window(la, lo, A, B, R)
        if not w:
            continue
        i, j = w
        flown = path_length(la[i:j + 1], lo[i:j + 1], ctx.dist)
        out[f"flown_{k}"] = flown
        out[f"gcd_{k}"] = float(ctx.dist(la[i], lo[i], la[j], lo[j]))
        out[f"time_{k}"] = (t[j] - t[i]) / 60
        out[f"ext_gc_{k}"] = flown - out[f"gcd_{k}"]
        if geom is not None:
            wr = ring_window(geom["rl"], geom["ro"], A, B, R)
            if wr:
                ref = path_length(geom["rl"][wr[0]:wr[1] + 1], geom["ro"][wr[0]:wr[1] + 1], ctx.dist)
                out[f"ref_{k}"] = ref
                ext = flown - ref
                out[f"ext_{k}"] = abs(ext) if p["HOR-03"] == "absolute" else ext
                out[f"extp_{k}"] = 100 * out[f"ext_{k}"] / ref if ref else np.nan
    # lateral deviation (FR-ANA-26), en-route portion of the main ring, 60 s samples
    R = float(p["HOR-01"])
    w = ring_window(la, lo, A, B, R)
    if geom is not None and w:
        i, j = w
        tt = np.arange(t[i], t[j], 60.0)
        sla = np.interp(tt, t, la)
        slo = np.interp(tt, t, lo)
        rl, ro = geom["rl"], geom["ro"]
        xt = np.empty(len(sla))
        for s0 in range(0, len(sla), 200):
            d = gc(sla[s0:s0 + 200, None], slo[s0:s0 + 200, None], rl[None, :], ro[None, :])
            xt[s0:s0 + 200] = d.min(1)
        xt_rows.append(pd.DataFrame({"fid": fid, "lat": sla, "lon": slo, "xt": xt}))
        out["xt_median"] = float(np.median(xt)) if len(xt) else np.nan
        out["xt_max"] = float(np.max(xt)) if len(xt) else np.nan


def terminal(tr, adep, ades, ctx: Ctx, out: dict):
    p = ctx.p
    la, lo, t = tr.lat.values, tr.lon.values, tr.t.values.astype(float)
    A, B = ctx.ap[adep], ctx.ap[ades]
    cum = cumulative(la, lo, ctx.dist)
    da, dd = gc(la, lo, *A), gc(la, lo, *B)
    for R in [float(x) for x in p["TER-01"]]:
        k = int(R)
        j = np.where(dd >= R)[0]
        if len(j):
            j = j[-1]
            out[f"arr_time_{k}"] = (t[-1] - t[j]) / 60
            out[f"arr_dist_{k}"] = cum[-1] - cum[j]
        i = np.where(da >= R)[0]
        if len(i):
            i = i[0]
            out[f"dep_time_{k}"] = (t[i] - t[0]) / 60
            out[f"dep_dist_{k}"] = cum[i]
    thr, rad = float(p["TER-03"][0]), float(p["TER-03"][1])
    m = dd <= rad
    out["net_turn"] = net_turn(la[m], lo[m])
    out["holding"] = bool(out["net_turn"] >= thr)


def vertical(tr, adep, ades, geom, plan, ctx: Ctx, out: dict, level_rows: list, change_rows: list, fid: str):
    p = ctx.p
    air = tr[tr.alt > 0]
    if len(air) < 30:
        out["vertical_ok"] = False
        return None
    rs = resample(air, float(p["GEN-06"]), float(p["GEN-07"]), float(p["VER-01"]))
    step = float(p["GEN-06"])
    alt, vr = rs["alt"], rs["vr"]
    A, B = ctx.ap[adep], ctx.ap[ades]
    d_dep = gc(rs["lat"], rs["lon"], *A)
    d_ades = gc(rs["lat"], rs["lon"], *B)
    lvl = (np.abs(vr) <= float(p["VER-02"])) & (alt > 1000)
    segs = segments(lvl)
    cruise_min = float(p["VER-04"]) * 60
    long = [(a, b) for a, b in segs if (b - a) * step >= cruise_min]
    if not long:
        out["vertical_ok"] = False
        return None
    toc_track, tod = long[0][0], long[-1][1] - 1
    toc = toc_track
    rfl = plan.get("rfl") if plan else None
    tol = float(p["CRU-05"])
    out["toc_rule"] = "track_only"
    if p["VER-06"] == "filed_level" and rfl:
        reach = np.where(alt >= rfl - tol)[0]
        if len(reach) and reach[0] < tod:
            toc = int(reach[0])
            out["toc_rule"] = "filed_level"
        else:
            out["rfl_not_reached"] = True
    out.update(vertical_ok=True, toc_min=(rs["t"][toc] - rs["t"][0]) / 60, toc_dist=float(d_dep[toc]),
               tod_to_end_min=(rs["t"][-1] - rs["t"][tod]) / 60, tod_dist=float(d_ades[tod]),
               max_alt=float(alt.max()))
    first_cruise = float(np.median(alt[long[0][0]:long[0][1]]))
    out["first_cruise"] = round(first_cruise / 100) * 100
    if rfl:
        out["first_vs_rfl"] = round((first_cruise - rfl) / 1000) * 1000
        lv = [rfl] + [c["level"] for c in (plan.get("changes") or []) if c.get("level")]
        out["max_vs_plan"] = round((alt.max() - max(lv)) / 1000) * 1000
    # capped climb (FR-ANA-42)
    l5 = [(a, b) for a, b in segs if (b - a) * step >= 300]
    if l5 and rfl:
        z = float(np.median(alt[l5[0][0]:l5[0][1]]))
        out["capped_climb"] = bool(z < rfl - float(p["VER-11"]))
        if out["capped_climb"]:
            out["capped_level"] = z
            out["capped_dist"] = float(d_dep[l5[0][0]])
            out["capped_min"] = (l5[0][1] - l5[0][0]) * step / 60
    else:
        out["capped_climb"] = False
    # level segments with phase (FR-ANA-40, 46, 47)
    min_d = min([float(x) for x in p["VER-03b"]] + [float(p["VER-03"])])
    for a, b in segs:
        dur = (b - a) * step
        if dur < min_d:
            continue
        phase = "climb" if b <= toc else ("descent" if a >= tod else "cruise")
        if phase == "cruise":
            continue
        level_rows.append({"fid": fid, "phase": phase, "dur": dur, "alt": float(np.median(alt[a:b])),
                           "d_adep": float(d_dep[a]), "d_ades": float(d_ades[a]), "lat": float(rs["lat"][a]),
                           "lon": float(rs["lon"][a]), "gap": bool(rs["gap"][a:b].any()),
                           "t_rel_min": (rs["t"][a] - rs["t"][0]) / 60})
    # cruise level changes (FR-ANA-45)
    step_ft = float(p["VER-05"])
    pw = []
    if plan and geom is not None:
        from .fpl import planned_profile  # noqa
        for c in plan.get("changes") or []:
            if c.get("level") and c["fix"] in plan.get("coords", {}):
                pw.append((c["fix"], c["level"], plan["coords"][c["fix"]]))
    n_changes = 0
    for (a, b), (c, d) in zip(long[:-1], long[1:]):
        z0, z1 = float(np.median(alt[a:b])), float(np.median(alt[c:d]))
        if abs(z1 - z0) < step_ft:
            continue
        n_changes += 1
        mid = (b + c) // 2
        same = [(f, gc(rs["lat"][mid], rs["lon"][mid], ll[0], ll[1])) for f, l, ll in pw if abs(l - z1) <= tol + 100]
        near = min(same, key=lambda x: x[1]) if same else None
        change_rows.append({"fid": fid, "lat": float(rs["lat"][mid]), "lon": float(rs["lon"][mid]), "from": z0,
                            "to": z1, "kind": "climb" if z1 > z0 else "descent",
                            "transition_min": (c - b) * step / 60, "n_planned": len(pw),
                            "reaches_planned": near is not None, "planned_fix": near[0] if near else "",
                            "dist_planned": near[1] if near else np.nan,
                            "matched": bool(near is not None and near[1] <= float(p["CRU-08"])),
                            "gap": bool(rs["gap"][b:c].any())})
    out["cruise_changes"] = n_changes
    out["planned_changes"] = len(pw)
    return {"rs": rs, "toc": toc, "tod": tod, "segs": segs, "d_dep": d_dep, "d_ades": d_ades}


def cruise(tr, adep, ades, geom, plan, vres, ctx: Ctx, out: dict, wp_rows: list, fid: str):
    """Metric 8 (leg-based, ENRI 13 Oct) and the v0.3 waypoint check (FR-ANA-43/44)."""
    p = ctx.p
    if geom is None or not plan or not plan.get("rfl"):
        return
    tol = float(p["CRU-05"])
    e1, e2 = [float(x) for x in p["CRU-06"]][:2]
    A, B = ctx.ap[adep], ctx.ap[ades]
    # --- waypoint check ---
    air = tr[tr.alt > 0]
    la, lo, t, alt = air.lat.values, air.lon.values, air.t.values.astype(float), air.alt.values.astype(float)
    win = float(p["CRU-02"]) * 60
    scope = float(p["CRU-01"])
    for n, (wlat, wlon), lvl in zip(geom["names"], geom["pts"][1:-1], geom["fix_levels"]):
        if lvl is None:
            continue
        if gc(wlat, wlon, *A) <= scope or gc(wlat, wlon, *B) <= scope:
            continue
        d = gc(la, lo, wlat, wlon)
        k = int(np.argmin(d))
        if d[k] > float(p["CRU-03"]):
            continue
        w = (t >= t[k] - win) & (t <= t[k] + win)
        dev = alt[k] - lvl
        cls = ("at" if abs(dev) <= e1 else ("above_1000" if dev > 0 and abs(dev) <= e2 else
               ("below_1000" if dev < 0 and abs(dev) <= e2 else ("above_2000" if dev > 0 else "below_2000"))))
        wp_rows.append({"fid": fid, "wp": n, "lat": wlat, "lon": wlon, "plan": lvl, "cpa_alt": float(alt[k]),
                        "cpa_dist": float(d[k]), "wmax": float(alt[w].max()), "wmin": float(alt[w].min()),
                        "dev": float(dev), "cls": cls, "plan_change": n in {c["fix"] for c in plan.get("changes", [])}})
    # --- leg-based score ---
    if not vres:
        return
    rs, toc, tod = vres["rs"], vres["toc"], vres["tod"]
    sl = slice(toc, tod + 1)
    sla, slo, salt, svr, sgap = rs["lat"][sl], rs["lon"][sl], rs["alt"][sl], rs["vr"][sl], rs["gap"][sl]
    if len(sla) < 2:
        return
    rl, ro, rcum = geom["rl"], geom["ro"], geom["rcum"]
    along = np.empty(len(sla))
    xt = np.empty(len(sla))
    for s0 in range(0, len(sla), 300):
        d = gc(sla[s0:s0 + 300, None], slo[s0:s0 + 300, None], rl[None, :], ro[None, :])
        k = d.argmin(1)
        along[s0:s0 + 300] = rcum[k]
        xt[s0:s0 + 300] = d[np.arange(len(k)), k]
    fix_cum = np.r_[0.0, geom["fix_cum"]]
    lv = [geom["rfl"]] + list(geom["fix_levels"])
    leg = np.clip(np.searchsorted(fix_cum, along, side="right") - 1, 0, len(lv) - 1)
    planned = np.array([lv[i] if lv[i] is not None else geom["rfl"] for i in leg], float)
    valid = (~sgap) & (xt <= float(p["CRU-04"])) & (np.abs(np.nan_to_num(svr, nan=99)) <= float(p["VER-02"]))
    dt_min = float(p["GEN-06"]) / 60
    dh = salt - planned
    v = valid
    above = v & (dh > tol)
    below = v & (dh < -tol)
    at = v & (np.abs(dh) <= tol)
    out["cruise_min_valid"] = float(v.sum() * dt_min)
    out["cruise_transition_min"] = float(((~sgap) & ~(np.abs(np.nan_to_num(svr, nan=99)) <= float(p["VER-02"]))).sum() * dt_min)
    out["time_at_min"] = float(at.sum() * dt_min)
    out["time_above_min"] = float(above.sum() * dt_min)
    out["time_below_min"] = float(below.sum() * dt_min)
    sa = float((np.abs(dh[above]) / 1000).sum() * dt_min)
    sb = float((np.abs(dh[below]) / 1000).sum() * dt_min)
    mode = p["CRU-07"]
    out["score_above"] = sa
    out["score_below"] = sb
    out["score_total"] = sb if mode == "below_only" else sa + sb
    out["score_mode"] = mode if mode != "openap_fuel" else "unit (OpenAP not installed)"
    if out["cruise_min_valid"]:
        out["share_above"] = out["time_above_min"] / out["cruise_min_valid"]
        out["share_below"] = out["time_below_min"] / out["cruise_min_valid"]
        out["share_at"] = out["time_at_min"] / out["cruise_min_valid"]


def crossings(tr, adep, ades, ctx: Ctx):
    p = ctx.p
    A, B = ctx.ap[adep], ctx.ap[ades]
    la, lo = tr.lat.values, tr.lon.values
    clip = float(p["CLU-01"])
    m = (gc(la, lo, *A) > clip) & (gc(la, lo, *B) > clip)
    if m.sum() < 2:
        m = np.ones(len(la), bool)
    return [crossing(la[m], lo[m], float(c), p["CLU-02b"]) for c in p["CLU-02"]]


def analyse_flight(fid, tr, adep, ades, plan, fixes, ctx: Ctx, sink: dict, polys=None) -> dict:
    out = {"fid": fid}
    geom = plan_geometry(plan, adep, ades, ctx, fixes) if plan and plan.get("usable") else None
    out["has_ref"] = geom is not None
    air = tr[tr.alt > 0].reset_index(drop=True)
    if len(air) < 10:
        out["error"] = "too few airborne points"
        return out
    out["airborne_min"] = (air.t.iloc[-1] - air.t.iloc[0]) / 60
    out["flown_total"] = path_length(air.lat.values, air.lon.values, ctx.dist)
    out["gc_total"] = float(ctx.dist(*ctx.ap[adep], *ctx.ap[ades]))
    out["cross"] = crossings(air, adep, ades, ctx)
    horizontal(air, adep, ades, geom, ctx, out, sink["xt"], fid)
    terminal(air, adep, ades, ctx, out)
    fir_split(air, geom, adep, ades, ctx, polys, fid, sink.setdefault("fir", []))
    vres = vertical(tr, adep, ades, geom, plan, ctx, out, sink["levels"], sink["changes"], fid)
    cruise(tr, adep, ades, geom, plan, vres, ctx, out, sink["waypoints"], fid)
    return out


def load_polygons(airspaces):
    """Airspace polygons as matplotlib Paths (lon, lat); MultiPolygons flattened."""
    from matplotlib.path import Path as MPath
    polys = []
    for a in airspaces:
        g = a.geometry or {}
        rings = []
        if g.get("type") == "Polygon":
            rings = [g["coordinates"][0]]
        elif g.get("type") == "MultiPolygon":
            rings = [p[0] for p in g["coordinates"]]
        for r in rings:
            polys.append((a.code, a.ansp, MPath(r)))
    return polys


def fir_split(air, geom, adep, ades, ctx: Ctx, polys, fid, rows: list):
    """FR-ANA-25: flown and reference distance and time per FIR inside the en-route window."""
    if not polys:
        return
    la, lo, t = air.lat.values, air.lon.values, air.t.values.astype(float)
    w = ring_window(la, lo, ctx.ap[adep], ctx.ap[ades], float(ctx.p["HOR-01"]))
    if not w:
        return
    i, j = w
    la, lo, t = la[i:j + 1], lo[i:j + 1], t[i:j + 1]
    pts = np.c_[lo, la]
    lab = np.array([""] * len(la), object)
    for code, ansp, path in polys:
        m = path.contains_points(pts) & (lab == "")
        lab[m] = code
    seg = ctx.dist(la[:-1], lo[:-1], la[1:], lo[1:])
    dt = np.diff(t) / 60
    ref = {}
    if geom is not None:
        wr = ring_window(geom["rl"], geom["ro"], ctx.ap[adep], ctx.ap[ades], float(ctx.p["HOR-01"]))
        if wr:
            rl, ro = geom["rl"][wr[0]:wr[1] + 1], geom["ro"][wr[0]:wr[1] + 1]
            rlab = np.array([""] * len(rl), object)
            for code, ansp, path in polys:
                m = path.contains_points(np.c_[ro, rl]) & (rlab == "")
                rlab[m] = code
            rseg = ctx.dist(rl[:-1], ro[:-1], rl[1:], ro[1:])
            for c in set(rlab[:-1]):
                ref[c] = float(rseg[rlab[:-1] == c].sum())
    ansp = {c: a for c, a, _ in polys}
    for c in set(lab[:-1]) | set(ref):
        m = lab[:-1] == c
        rows.append({"fid": fid, "fir": c or "(outside)", "ansp": ansp.get(c, ""), "flown": float(seg[m].sum()),
                     "time": float(dt[m].sum()), "ref": ref.get(c, np.nan)})
