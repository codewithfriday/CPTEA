"""Rule-based insight drafting (SRS 8.2, INS-01..14)."""
import numpy as np


def _th(params, k):
    return float(params["REP-02"].get(k))


def evaluate(S: dict, params: dict, project) -> list:
    out = []

    def add(rule, title, text, numbers, chart, effect, kind="insight"):
        out.append(dict(rule=rule, title=title, drafted=text, edited="", numbers=numbers, chart=chart,
                        effect=float(effect), status="drafted", kind=kind))

    main = S["main_ring"]
    ext = {e["dirn"]: e for e in S["extension"] if e["ring"] == main}
    var_en = {e["dirn"]: e.get("time_var") for e in S["extension"] if e["ring"] == main}
    ter = {t["ades"]: t for t in S["terminal"]["by_ades"] if t["ring"] == S["terminal"]["big_ring"]}

    # INS-01 arrival variance vs en-route variance
    for d, e in ext.items():
        ades = d.split("-")[1]
        t = ter.get(ades)
        ve = var_en.get(d)
        if t and ve and t["var"] / ve > _th(params, "INS-01"):
            add("INS-01", f"The arrival into {ades} is the least predictable part of the flight",
                f"The last {t['ring']} NM into {ades} varies ±{t['var']:.1f} min, against ±{ve:.1f} min for the whole "
                f"en-route portion ({d}). The main inefficiency on this direction is the arrival.",
                {"arrival_var": t["var"], "enroute_var": ve, "dirn": d}, "terminal", t["var"] / ve)
    # INS-02 holding share
    for ap, t in ter.items():
        hs = 100 * (t.get("hold_share") or 0)
        if hs > _th(params, "INS-02"):
            others = [f"{100 * (o.get('hold_share') or 0):.0f}% into {a}" for a, o in ter.items() if a != ap]
            add("INS-02", f"Holding-type turns are frequent at {ap}",
                f"{hs:.0f}% of {ap} arrivals show holding-type turns (net turn ≥ {params['TER-03'][0]:.0f}° within "
                f"{params['TER-03'][1]:.0f} NM)" + (f", against {', '.join(others)}." if others else "."),
                {"ades": ap, "hold_pct": hs}, "terminal", hs / 10)
    # INS-03 upstream absorption
    for ap, r in (S["terminal"].get("corr") or {}).items():
        if r is not None and abs(r) < _th(params, "INS-03"):
            add("INS-03", f"Delay at {ap} is not absorbed upstream",
                f"En-route time does not respond to arrival delay at {ap} (r = {r:.2f}): flights arrive at normal speed "
                f"and absorb delay in the terminal area, the most expensive place to absorb it.",
                {"ades": ap, "r": r}, "terminal_scatter", 1 - abs(r))
    # INS-04 negative extension
    for d, e in ext.items():
        if e["median"] is not None and e["median"] < _th(params, "INS-04"):
            add("INS-04", f"Flights fly shorter than filed ({d})",
                f"Between the {main} NM rings, flights fly a median {abs(e['median']):.1f} NM shorter than their filed route; "
                f"{100 * e['shorter_share']:.0f}% are shorter than filed, mainly through tactical directs.",
                {"dirn": d, "median": e["median"], "shorter_share": e["shorter_share"]}, "extension", abs(e["median"]) / 5)
    # INS-05 sign change across rings
    for d in S["dirs"]:
        vals = {e["ring"]: e["median"] for e in S["extension"] if e["dirn"] == d and e["median"] is not None}
        if vals and min(vals.values()) < 0 < max(vals.values()):
            parts = ", ".join(f"{v:+.0f} NM at {r} NM rings" for r, v in sorted(vals.items()))
            add("INS-05", f"Path extension changes sign with the boundary ({d})",
                f"Median extension against the filed route is {parts}. The en-route boundary must always be stated.",
                {"dirn": d, "values": vals}, "extension_rings", 2)
    # INS-06 single tight cluster
    cl = S.get("clusters", {}).get("summary", [])
    for d in S["dirs"]:
        cd = [c for c in cl if c["dirn"] == d and not c["contingency"]]
        xs = [c for c in S.get("clusters", {}).get("crossings", []) if c["dirn"] == d and not c["outlier"]]
        if len(cd) == 1 and xs:
            arr = np.array([x["x"] for x in xs if x["x"] and None not in x["x"]], float)
            if len(arr):
                spread = float(np.nanmax(np.nanstd(arr, axis=0)))
                if spread < _th(params, "INS-06"):
                    add("INS-06", f"One route only ({d})",
                        f"One nominal route in this direction; crossing positions vary by at most {spread:.2f}° (standard deviation). "
                        f"Clustering is only needed to remove outliers.", {"dirn": d, "spread": spread}, "clusters", 0.5)
    # INS-07 cruise above filed
    for c in S["cruise"]["classes"]:
        above = 100 * (c.get("above_2000", 0) + c.get("above_1000", 0))
        if above > _th(params, "INS-07"):
            add("INS-07", f"Cruise often above the filed level ({c['dirn']}, {c['type']})",
                f"{c['type']} flights ({c['dirn']}) cruise above the filed level at {above:.0f}% of cruise waypoints "
                f"({100 * c.get('above_2000', 0):.0f}% by 2,000 ft or more). The filed profile is conservative, so the cruise "
                f"score must keep its sign.", {"dirn": c["dirn"], "type": c["type"], "above_pct": above}, "cruise", above / 20)
    # INS-08 level changes vs planned
    ch = S["cruise"].get("changes") or {}
    if ch.get("matched_share") is not None and 100 * ch["matched_share"] < _th(params, "INS-08"):
        add("INS-08", "Level changes rarely happen at the filed waypoint",
            f"Only {100 * ch['matched_share']:.0f}% of flown level changes that reach a filed level happen within "
            f"{params['CRU-08']:.0f} NM of the waypoint where it was filed (median {ch.get('median_dist_planned') or 0:.0f} NM away); "
            f"{100 * ch['on_unplanned_share']:.0f}% of flown changes are on flights that filed none.",
            {"matched_share": ch["matched_share"]}, "cruise_changes", 2)
    # INS-09 capped climbs
    caps = [c for c in S["cruise"]["capped"] if 100 * c["share"] > _th(params, "INS-09")]
    if caps:
        lo, hi = min(c["share"] for c in caps), max(c["share"] for c in caps)
        add("INS-09", "Climbs are often capped below the filed level",
            f"{100 * lo:.0f}–{100 * hi:.0f}% of flights (in {len(caps)} direction/type groups) are held below their filed "
            f"initial level for at least 5 minutes during climb.", {"groups": caps}, "cruise", hi * 10)
    # INS-10 threshold sensitivity
    sens = S["leveloffs"]["sensitivity"]
    for (ph, ap) in {(s["phase"], s["airport"]) for s in sens}:
        ss = {s["dur"]: s["share"] for s in sens if s["phase"] == ph and s["airport"] == ap}
        a, b = ss.get(min(ss)), ss.get(120.0) or ss.get(max(ss))
        if a and b is not None and (a - b) / a * 100 > _th(params, "INS-10"):
            word = "into" if ph == "descent" else "out of"
            add("INS-10", f"Level-off counts depend on the duration threshold ({ph} {word} {ap})",
                f"The share of flights with a {ph} level-off {word} {ap} falls from {100 * a:.0f}% (≥ {min(ss):.0f} s) to "
                f"{100 * b:.0f}% (≥ 120 s). The threshold must be fixed and time spent level reported.",
                {"phase": ph, "airport": ap, "share_min": a, "share_120": b}, "leveloff_sensitivity", a * 3)
    # INS-11 dominant band
    for (ph, ap) in {(b["phase"], b["airport"]) for b in S["leveloffs"]["bands"]}:
        bb = [b for b in S["leveloffs"]["bands"] if b["phase"] == ph and b["airport"] == ap]
        tot = sum(b["min_per_flight"] for b in bb)
        if tot <= 0:
            continue
        top = max(bb, key=lambda b: b["min_per_flight"])
        if 100 * top["min_per_flight"] / tot > _th(params, "INS-11"):
            word = "into" if ph == "descent" else "out of"
            add("INS-11", f"Level time concentrates at {top['band']} ({ph} {word} {ap})",
                f"{top['band']} holds {100 * top['min_per_flight'] / tot:.0f}% of {ph} level time {word} {ap} "
                f"({top['min_per_flight']:.2f} min per flight, {top['flights']} flights).",
                {"phase": ph, "airport": ap, "band": top["band"]}, "leveloff_hist", top["min_per_flight"])
    # INS-12 seasonal peaks
    for ap in {m["ades"] for m in S["terminal"]["monthly"]}:
        ms = [m for m in S["terminal"]["monthly"] if m["ades"] == ap and m["n"] >= 5]
        if not ms:
            continue
        mean = np.mean([m["add_mean"] for m in ms])
        peaks = [m["month"] for m in ms if m["add_mean"] > _th(params, "INS-12") * mean]
        if peaks:
            add("INS-12", f"Seasonal peaks in arrival delay at {ap}",
                f"Months with additional arrival time above {_th(params, 'INS-12'):.1f}× the period mean ({mean:.1f} min): "
                f"{', '.join(peaks)}.", {"ades": ap, "peaks": peaks, "mean": mean}, "terminal_monthly", len(peaks) / 2)
    # INS-13 aircraft types differ
    for d in S["dirs"]:
        ts = [c for c in S["cruise"]["classes"] if c["dirn"] == d and c["n_flights"] >= 10]
        if len(ts) >= 2:
            vals = [100 * (c["above_2000"] + c["above_1000"]) for c in ts]
            if max(vals) - min(vals) > _th(params, "INS-13"):
                add("INS-13", f"Aircraft types behave differently ({d})",
                    f"Share of cruise above filed ranges from {min(vals):.0f}% to {max(vals):.0f}% between aircraft types "
                    f"({', '.join(c['type'] for c in ts)}). Report results by type.", {"dirn": d}, "cruise", 1)
    # INS-14 coverage
    cov = S.get("coverage") or {}
    if cov.get("counts"):
        low = []
        for cs, row in zip(cov["callsigns"], cov["counts"]):
            mx = max(row) if row else 0
            for m, n in zip(cov["months"], row):
                if mx and n < _th(params, "INS-14") / 100 * mx:
                    low.append(f"{cs} {m}")
        if low:
            add("INS-14", "Data coverage is uneven",
                f"{len(low)} callsign-months have fewer than {_th(params, 'INS-14'):.0f}% of the usual number of flights "
                f"(for example {', '.join(low[:4])}). Interpret direction and monthly results with care.",
                {"low": low[:50]}, "coverage", len(low) / 10)
    out.sort(key=lambda x: -x["effect"])
    return out
