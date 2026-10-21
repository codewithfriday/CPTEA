"""Plain-language report: every table and chart of a run, each explained for a non-specialist reader.

Each element is followed by three short parts:
  What this shows  - what was measured, in everyday words
  How to read it   - how to look at the table or chart
  What it tells us - the run's own numbers turned into sentences
Insights reviewed by analysts are attached to the element they belong to; where a section has none, the insights
CPTEA proposed (not yet reviewed) are used and labelled as such.
"""
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from ..models import Dataset, Insight, ParameterSet, Project, Run
from . import charts

NM_KM = 1.852


# ------------------------------------------------------------------ wording helpers
def n0(v):
    return "–" if v is None else f"{v:,.0f}"


def n1(v):
    return "–" if v is None else f"{v:,.1f}"


def pc(v, d=0):
    return "–" if v is None else f"{100 * v:.{d}f}%"


def nm(v):
    """Distance with its metric equivalent."""
    if v is None:
        return "–"
    return f"{abs(v):,.1f} NM (about {abs(v) * NM_KM:,.0f} km)"


def share_words(s):
    """0.47 -> 'almost half'."""
    if s is None:
        return "an unknown share"
    for lim, w in [(0.03, "almost none"), (0.08, "very few"), (0.15, "about one in ten"), (0.22, "about one in five"),
                   (0.29, "about a quarter"), (0.38, "about a third"), (0.46, "a little under half"), (0.54, "about half"),
                   (0.62, "a little over half"), (0.71, "about two thirds"), (0.8, "about three quarters"),
                   (0.92, "most"), (0.985, "nearly all")]:
        if s < lim:
            return w
    return "all"


def arrow(d):
    return d.replace("-", " → ")


def fl(ft):
    return "–" if ft is None else f"FL{int(round(ft / 100)):03d}"


# What each kind of insight means, for a reader who is not an analyst. Shown under the insight's own wording.
PLAIN = {
    "INS-01": "The arrival is the least predictable part of the flight: flight times vary more in the last part than over the whole en-route phase.",
    "INS-02": "Many arrivals had to circle before landing, a sign of waiting for a landing slot.",
    "INS-03": "Flights do not slow down early when the destination is busy; they arrive at normal speed and wait near the airport, which costs more fuel.",
    "INS-04": "Aircraft flew a shorter path than the route in their flight plan, usually thanks to direct routings from air traffic control.",
    "INS-05": "Whether flights look longer or shorter than planned depends on where the en-route part is taken to start and end, so the boundary must be stated with the result.",
    "INS-06": "Almost all flights use the same route; route choice is not a source of inefficiency.",
    "INS-07": "These aircraft often cruised higher than they had planned. Flying higher is usually as good or better for fuel, so it is not counted as a loss.",
    "INS-08": "Climbs and descents during the cruise are decided in flight, rather than happening where the flight plan said they would.",
    "INS-09": "Some flights were kept below the level they asked for after take-off, usually for traffic reasons; that costs fuel until they can climb.",
    "INS-10": "The number of level-offs depends strongly on how long a pause in the climb or descent must be before it counts.",
    "INS-11": "Most of the time spent flying level is at these heights, which shows where in the climb or descent aircraft are usually stopped.",
    "INS-12": "Some months had clearly more arrival delay than usual, pointing to seasonal traffic or weather.",
    "INS-13": "Different aircraft types fly the cruise quite differently, so results should be read type by type.",
    "INS-14": "Some months have little data for some flight numbers, so month-by-month comparisons need care.",
}
GROUP_TITLES = {
    "INS-01": "Arrival predictability", "INS-02": "Holding before landing", "INS-03": "Where delay is absorbed",
    "INS-04": "Shorter than filed", "INS-05": "Effect of the boundary choice", "INS-06": "Route use",
    "INS-07": "Cruising above the filed level", "INS-08": "Level changes during the cruise", "INS-09": "Capped climbs",
    "INS-10": "Level-off threshold", "INS-11": "Heights of level flight", "INS-12": "Seasonal arrival delay",
    "INS-13": "Differences between aircraft types", "INS-14": "Data coverage",
}
# order of importance for the summary: one insight per rule
SUMMARY_ORDER = ["INS-01", "INS-02", "INS-03", "INS-04", "INS-09", "INS-08", "INS-07", "INS-12", "INS-10", "INS-05",
                 "INS-13", "INS-11", "INS-06", "INS-14"]


class Doc:
    """Ordered blocks; the PDF renderer keeps every heading with what follows it."""

    def __init__(self):
        self.blocks = []

    def h1(self, t):
        self.blocks.append({"t": "h", "level": 1, "text": t})

    def h2(self, t):
        self.blocks.append({"t": "h", "level": 2, "text": t})

    def p(self, t):
        self.blocks.append({"t": "p", "text": t})

    def ul(self, items):
        items = [i for i in items if i]
        if items:
            self.blocks.append({"t": "ul", "items": items})

    def table(self, head, rows, widths=None, note=None):
        self.blocks.append({"t": "table", "head": head, "rows": rows, "widths": widths, "caption": note})

    def img(self, path, caption):
        if path:
            self.blocks.append({"t": "img", "path": str(path), "caption": caption})
            return True
        return False

    def explain(self, what, read, means):
        means = [m for m in (means if isinstance(means, list) else [means]) if m]
        self.blocks.append({"t": "explain", "what": what, "read": read, "means": means})

    def insights(self, items):
        seen = set()
        for i in items:
            plain = "" if i.get("rule") in seen else i.get("plain", "")
            seen.add(i.get("rule"))
            self.blocks.append({"t": "insight", "title": i["title"], "text": i["text"], "proposed": i["proposed"],
                                "plain": plain})

    def keyfacts(self, facts):
        self.blocks.append({"t": "keyfacts", "items": facts})


# ------------------------------------------------------------------ content
def build(db, run: Run, opts: dict, out_dir: Path) -> dict:
    from ..engine import aggregate
    from ..engine.fpl import expand_route
    from ..engine.pipeline import load_navdb
    import pyarrow.dataset as pads

    project: Project = db.get(Project, run.project_id)
    ds: Dataset = db.get(Dataset, run.dataset_id)
    ps: ParameterSet = db.get(ParameterSet, run.paramset_id)
    P = ps.values
    rd = config.run_dir(run.id)
    filters = opts.get("filters") or {}
    S = aggregate.compute(rd, P, project, filters, ds.quality)
    meta = json.loads((rd / "meta.json").read_text())
    res = aggregate.apply_filters(pd.read_parquet(rd / "flights.parquet"), filters)
    main = S["main_ring"]
    big = S["terminal"]["big_ring"]
    A, B_ = project.adep, project.ades

    # ---- insights: reviewed first; proposed ones where a section has none reviewed
    allins = db.query(Insight).filter_by(run_id=run.id).order_by(Insight.effect.desc()).all()
    reviewed = [i for i in allins if i.status in ("accepted", "edited")]
    proposed = [i for i in allins if i.status == "drafted"]

    def ins_dict(i):
        return {"title": i.title or "", "text": i.edited or i.drafted, "proposed": i.status == "drafted", "chart": i.chart,
                "rule": i.rule, "kind": i.kind, "plain": PLAIN.get(i.rule, "")}

    used = set()

    def ins_for(*chart_keys, limit=3):
        rv = [i for i in reviewed if i.chart in chart_keys and i.id not in used and i.kind == "insight"]
        pool = rv or [i for i in proposed if i.chart in chart_keys and i.id not in used and i.kind == "insight"]
        pick = pool[:limit]
        used.update(i.id for i in pick)
        return [ins_dict(i) for i in pick]

    # ---- figures (same data as the Results page)
    fx, aw = load_navdb(db)
    fids = res.fid.tolist()
    pts = pads.dataset(config.dataset_dir(run.dataset_id) / "points.parquet").to_table(
        columns=["fid", "t", "lat", "lon", "alt"], filter=pads.field("fid").isin(fids)).to_pandas()
    tracks, meta_r = [], res.set_index("fid")
    for fid, g in pts.groupby("fid", sort=False):
        g = g.sort_values("t")
        g = g[g.alt > 0] if (g.alt > 0).sum() > 5 else g
        idx = np.linspace(0, len(g) - 1, min(len(g), 50)).astype(int)
        tracks.append({"dirn": meta_r.loc[fid].dirn, "outlier": bool(meta_r.loc[fid].outlier),
                       "coords": np.c_[g.lat.values[idx], g.lon.values[idx]].tolist()})
    ap = {A: (project.adep_lat, project.adep_lon), B_: (project.ades_lat, project.ades_lon)}
    routes = {}
    for d, g in res.groupby("dirn"):
        rk = g.route_key.dropna()
        if len(rk):
            top = Counter(rk).most_common(1)[0][0]
            names, _, _ = expand_route(top, dict(fx), aw)
            a, b = d.split("-")
            routes[d] = [(top, [ap[a]] + [fx[n] for n in names if n in fx] + [ap[b]])]
    F = {}

    def fig(key, fn, *a):
        try:
            F[key] = fn(*a)
        except Exception:  # noqa: BLE001  (a chart that cannot be drawn is left out, the text explains it)
            F[key] = None

    fig("map", charts.track_map, tracks, routes, ap, out_dir / "x_map.png") if tracks else None
    fig("clu", charts.cluster_monthly, S, out_dir / "x_clu.png")
    fig("ext", charts.extension_rings, S, out_dir / "x_ext.png") if S["extension"] else None
    fig("ext_type", charts.extension_by_type, S, out_dir / "x_ext_type.png")
    fig("enr", charts.enroute_monthly, S, out_dir / "x_enr.png") if S["enroute_monthly"] else None
    fig("lat", charts.lateral, S, out_dir / "x_lat.png") if S["lateral"] else None
    fig("ter", charts.terminal_monthly, S, out_dir / "x_ter.png") if S["terminal"]["monthly"] else None
    fig("ter_sc", charts.terminal_scatter, S, out_dir / "x_ter_sc.png")
    fig("cru", charts.cruise_classes, S, out_dir / "x_cru.png")
    fig("chg", charts.cruise_changes_map, S, tracks, out_dir / "x_chg.png")
    fig("los", charts.leveloff_sensitivity, S, out_dir / "x_los.png") if S["leveloffs"]["sensitivity"] else None
    fig("loh", charts.leveloff_hist, S, out_dir / "x_loh.png", "descent")
    fig("loc", charts.leveloff_hist, S, out_dir / "x_loc.png", "climb")

    D = Doc()
    ext_main = {e["dirn"]: e for e in S["extension"] if e["ring"] == main}
    ter_big = {t["ades"]: t for t in S["terminal"]["by_ades"] if t["ring"] == big}
    lo = {(p["phase"], p["airport"]): p for p in S["leveloffs"]["primary"]}
    cov = S.get("coverage") or {}
    months = cov.get("months") or []
    period = f"{months[0]} to {months[-1]}" if months else "the loaded period"

    # =========================================================== 1. About
    D.h1("1. About this report")
    D.p(f"This report explains, in everyday language, the results of analysis run #{run.id} for flights between "
        f"{A} and {B_}. It looks at {n0(S['n']['flights'])} flights from {period} and asks one question: how close did "
        f"each flight come to the most efficient way of flying it?")
    D.p("Efficiency is judged in three ways. Sideways: did aircraft fly a longer or shorter path than the route they "
        "filed? In time: how long and how predictable were the en-route and arrival phases? Vertically: did aircraft "
        "climb, cruise and descend smoothly at the levels they asked for, or did they have to stop and fly level on the "
        "way up or down?")
    D.p("Every table and chart below is followed by a short explanation in three parts: what it shows, how to read it, "
        "and what it tells us about these flights. Distances are in nautical miles (NM); one nautical mile is 1.852 km. "
        "Altitudes are in feet, and flight levels such as FL370 mean 37,000 feet.")
    if filters:
        D.p("The results are limited to the filters set on the Results page: " +
            "; ".join(f"{k.replace('_', ' ')} {v}" for k, v in filters.items()) + ".")

    # =========================================================== 2. Main findings
    D.h1("2. The main findings")
    facts = []
    for d, e in ext_main.items():
        if e.get("median") is None:
            continue
        word = "shorter" if e["median"] < 0 else "longer"
        facts.append(f"{arrow(d)}: between the {main} NM boundaries, the typical flight flew {nm(e['median'])} {word} "
                     f"than its filed route ({pc(e['shorter_share'])} of flights flew shorter than filed).")
    for a2, t in ter_big.items():
        facts.append(f"Arrivals into {a2}: the last {big} NM took a typical {n1(t['median'])} minutes, about "
                     f"{n1(t['add_mean'])} minutes more on average than an unhindered arrival; {pc(t['hold_share'])} of "
                     f"arrivals made holding-type turns.")
    for a2 in (B_, A):
        p_ = lo.get(("descent", a2))
        if p_:
            facts.append(f"Descents into {a2}: {pc(p_['share'])} of flights levelled off at least once on the way down "
                         f"(using a minimum of {P['VER-03']:.0f} seconds of level flight).")
    cls = S["cruise"]["classes"]
    if cls:
        wp = sum(c["n_wp"] for c in cls)
        at = sum(c["at"] * c["n_wp"] for c in cls) / wp if wp else None
        above = sum((c["above_1000"] + c["above_2000"]) * c["n_wp"] for c in cls) / wp if wp else None
        below = sum((c["below_1000"] + c["below_2000"]) * c["n_wp"] for c in cls) / wp if wp else None
        facts.append(f"Cruise: at the waypoints of the flight plan, aircraft were at their filed level {pc(at)} of the "
                     f"time, higher {pc(above)} and lower {pc(below)}.")
    D.keyfacts(facts)
    pool = [i for i in reviewed if i.kind == "insight"] or [i for i in proposed if i.kind == "insight"]
    lead = []
    for rule in SUMMARY_ORDER:
        i = next((x for x in pool if x.rule == rule), None)
        if i:
            lead.append(i)
    lead += [i for i in pool if i.rule not in SUMMARY_ORDER]
    lead = lead[:6]
    if lead:
        D.p("What the analysis found, " + ("as reviewed by the analysts:" if reviewed else
                                           "as proposed by CPTEA from the numbers (not yet reviewed by an analyst):"))
        D.ul([(f"{i.title}: " if i.title else "") +
              ((PLAIN[i.rule][0].lower() + PLAIN[i.rule][1:]) if i.rule in PLAIN and i.status == "drafted"
               else (i.edited or i.drafted)) for i in lead])

    # =========================================================== 3. Data
    D.h1("3. The data behind the results")
    D.h2("Flights per month")
    if months:
        D.table(["Callsign"] + [m[2:] for m in months],
                [[cs] + [str(x) for x in row] for cs, row in zip(cov["callsigns"], cov["counts"])],
                [2.2] + [1] * len(months), "Number of flights per callsign and month (year-month).")
        empty = [f"{cs} ({sum(1 for x in row if x == 0)} months)" for cs, row in zip(cov["callsigns"], cov["counts"])
                 if any(x == 0 for x in row)]
        partial = [f"{cs} {m} ({x})" for cs, row in zip(cov["callsigns"], cov["counts"]) if row and max(row)
                   for m, x in zip(months, row) if 0 < x < 0.5 * max(row)]
        D.explain(
            "How many recorded flights the analysis has for each flight number in each month.",
            "Each row is one flight number (callsign), each column a month. A daily flight shows about 30 per month; "
            "a 0 means no recording was available for that month.",
            [f"In total {n0(S['n']['loaded'])} flights were loaded and {n0(S['n']['included'])} kept for analysis; "
             f"{n0(S['n']['with_plan'])} could be matched to their flight plan and {n0(S['n']['with_ref'])} had a filed "
             f"route that could be drawn on the map waypoint by waypoint.",
             ("Months without data for some callsigns: " + ", ".join(empty) + ". This usually reflects schedule "
              "changes (a flight number not operated that season) rather than missing recordings, but results for those "
              "flight numbers cover a shorter period.") if empty else "Every callsign has data in every month.",
             ("Months with unusually few flights (number in brackets): " + ", ".join(partial) + ". A first or last "
              "month that is only partly covered is normal; others are worth checking before comparing month by "
              "month.") if partial else None])
    q = ds.quality or {}
    g = q.get("gaps") or {}
    D.ul([f"Complete gate-to-gate recordings (from the departure airport to the arrival airport): {pc(q.get('gate_to_gate_share'), 1)}.",
          f"Recordings with a gap of more than {g.get('threshold_s')} seconds in the air: {g.get('flights')} flights; "
          f"more than {g.get('major_threshold_s')} seconds: {g.get('flights_major')} flights." if g else None])
    D.h2("Where the flights flew")
    if D.img(F.get("map"), "Every flight track (thin lines) and the most frequently filed route (thick line), per direction."):
        out_n = S["n"]["outliers"]
        D.explain(
            "The actual paths of all flights, drawn from the surveillance recordings, with the route most often "
            "written in the flight plans on top.",
            "Each thin line is one flight; where many lines overlap the colour becomes darker, showing the usual path. "
            "Red lines are flights that went a clearly different way (outliers).",
            [f"The tracks form one tight corridor in each direction, close to the filed route." if out_n <= 0.02 * S["n"]["flights"]
             else f"The tracks spread over more than one corridor.",
             f"{out_n} flight{'s' if out_n != 1 else ''} took a clearly different path (for example around weather) and "
             f"{'are' if out_n != 1 else 'is'} kept in the results but marked as outlier{'s' if out_n != 1 else ''}."])
    D.insights(ins_for("coverage", "map"))

    # =========================================================== 4. Routes
    D.h1("4. Which routes were used")
    cs = S["clusters"]["summary"] if S.get("clusters") else []
    if cs:
        D.h2("Route groups")
        D.table(["Direction", "Route group", "Flights", "Share", "Outliers", "Rarely used route"],
                [[arrow(c["dirn"]), str(c["cluster"] + 1), str(c["n"]), pc(c["share"]), str(c["outliers"]),
                  "yes" if c["contingency"] else "no"] for c in cs], [3, 2, 1.5, 1.5, 1.5, 2.2])
        per_dir = defaultdict(list)
        for c in cs:
            per_dir[c["dirn"]].append(c)
        means = []
        for d, lst in per_dir.items():
            if len(lst) == 1:
                means.append(f"{arrow(d)}: all flights used the same route group, so route choice is not a source of "
                             f"inefficiency in this direction.")
            else:
                top = max(lst, key=lambda c: c["share"])
                means.append(f"{arrow(d)}: {len(lst)} route groups; the main one carries {pc(top['share'])} of flights.")
        cut = ", ".join(f"{x:g}°" for x in S["clusters"].get("cuts", []))
        D.explain(
            "Flights are grouped by where they cross a set of imaginary lines across the route "
            f"({'latitudes ' + cut if cut else 'cut lines'}). Flights crossing at similar places are put in the same route group.",
            "One row per route group. \"Share\" is the part of that direction's flights in the group. A group with very few "
            "flights is marked as a rarely used route; outliers are flights that fit no group well.",
            means)
    if D.img(F.get("clu"), "Flights per route group and month."):
        D.explain("How many flights used each route group in each month.",
                  "Each bar is one month; colours are directions and route groups. Bars that change height show changes in "
                  "the schedule; a new colour appearing would show a new route coming into use.",
                  "The bars follow the flight schedule: the route used does not change through the year." if
                  all(len(v) == 1 for v in per_dir.values()) else
                  "Look for months where a second route group appears: those are periods when traffic was routed differently.")
    D.insights(ins_for("clusters"))

    # =========================================================== 5. Horizontal
    D.h1("5. Flying further or shorter than planned")
    D.p("This section compares the distance actually flown with the length of the route filed in the flight plan. "
        "Only the en-route part counts: the flight is cut at circles around both airports (the \"boundary rings\", here "
        f"{', '.join(str(int(r)) for r in S['rings'])} NM), so that taxiing, departure and arrival procedures do not "
        f"distort the comparison. The main results use the {main} NM rings.")
    if S["extension"]:
        D.h2("Filed route against flown distance")
        D.table(["Rings", "Direction", "Flights", "Filed route", "Flown", "Flown − filed", "Middle 70% of flights",
                 "Shorter than filed", "Time (min)", "Time spread ±"],
                [[f"{e['ring']} NM", arrow(e["dirn"]), str(e["n"]), n1(e["ref_median"]), n1(e["flown_median"]),
                  f"{e['median']:+.1f}" if e["median"] is not None else "–", f"{n1(e['p15'])} to {n1(e['p85'])}",
                  pc(e["shorter_share"]), n1(e["time_median"]), n1(e["time_var"])] for e in S["extension"]],
                [1.2, 2.3, 1.2, 1.4, 1.4, 1.5, 2.1, 1.5, 1.3, 1.3],
                "Distances in NM, typical (median) values. Difference = flown minus filed.")
        means = []
        for d, e in ext_main.items():
            if e.get("median") is None:
                continue
            if e["median"] < -0.5:
                means.append(f"{arrow(d)}: flights typically flew {nm(e['median'])} less than filed between the {main} NM "
                             f"rings. {pc(e['shorter_share'])} of flights flew shorter than filed. Shorter-than-filed paths "
                             f"usually come from controllers clearing aircraft direct to a later waypoint: a saving compared "
                             f"with the plan.")
            elif e["median"] > 0.5:
                means.append(f"{arrow(d)}: flights typically flew {nm(e['median'])} more than filed, for example because "
                             f"of vectoring or weather avoidance.")
            else:
                means.append(f"{arrow(d)}: flights flew almost exactly the filed distance.")
            if e.get("time_var") is not None:
                means.append(f"The en-route time in this direction was typically {n1(e['time_median'])} minutes, and most "
                             f"flights were within ±{n1(e['time_var'])} minutes of it, so the en-route phase is "
                             f"{'very ' if e['time_var'] < 4 else ''}predictable.")
        D.explain(
            "For each direction and each pair of boundary rings: the typical length of the filed route, the typical "
            "distance actually flown, and the difference between the two.",
            "A negative difference means aircraft flew less than they filed; a positive one means they flew more. "
            "\"Middle 70%\" gives the range that covers most flights, leaving out the 15% at each extreme. \"Time spread\" "
            "is half the width of that range for the en-route time: the smaller it is, the more predictable the flight.",
            means)
    if D.img(F.get("ext"), "Difference between flown and filed distance for each pair of boundary rings."):
        flip = [d for d in S["dirs"] if len({np.sign(e["median"]) for e in S["extension"] if e["dirn"] == d and e["median"]}) > 1]
        D.explain(
            "The same difference as in the table, drawn for each choice of boundary ring.",
            "The dot is the typical flight, the bar the middle 70% of flights. Dots left of the vertical line mean "
            "shorter than filed, right of it longer than filed.",
            ("The result changes sign with the boundary for " + ", ".join(arrow(d) for d in flip) + ": close to the "
             "airports flights fly further than filed (departure and arrival paths are longer than the straight lines in "
             "the plan), while en-route they fly shorter. The choice of boundary must therefore always be stated with "
             "the result.") if flip else "The direction of the result is the same whichever boundary is used.")
    D.insights(ins_for("extension", "extension_rings"))
    if D.img(F.get("ext_type"), "Difference between flown and filed distance by aircraft type."):
        boxes = [b for b in S["extension_box"] if b["by"] == "dirn+type" and b["n"] >= 3]
        spread = sorted(boxes, key=lambda b: b["median"])
        D.explain(
            f"The flown-minus-filed distance between the {main} NM rings, split by aircraft type.",
            "Each row is one aircraft type in one direction, with the number of flights in brackets. The shaded box holds "
            "the middle half of the flights and the thick line is the typical flight.",
            (f"The typical difference ranges from {spread[0]['median']:+.1f} NM ({spread[0]['key']}) to "
             f"{spread[-1]['median']:+.1f} NM ({spread[-1]['key']}). Types with only a few flights should not be "
             f"compared too closely.") if len(spread) > 1 else None)
    if D.img(F.get("enr"), "En-route time per month: typical value and the range of most flights."):
        em = S["enroute_monthly"]
        means = []
        for d in S["dirs"]:
            rows = [r for r in em if r["dirn"] == d and r["n"] >= 5]
            if rows:
                lo_, hi_ = min(rows, key=lambda r: r["median"]), max(rows, key=lambda r: r["median"])
                means.append(f"{arrow(d)}: the typical en-route time varies from {n1(lo_['median'])} minutes "
                             f"({lo_['month']}) to {n1(hi_['median'])} minutes ({hi_['month']}). Month-to-month changes "
                             f"of a few minutes are mostly the effect of seasonal winds.")
        D.explain("How long the en-route phase took, month by month.",
                  "The line is the typical flight, the band the middle 70% of flights. A wide band means less predictable "
                  "flight times that month.", means)
    if D.img(F.get("lat"), "Distance from the filed route along the way."):
        lat = S["lateral"]
        means = []
        for d in S["dirs"]:
            rows = [r for r in lat if r["dirn"] == d and r["median"] is not None]
            if rows:
                mx = max(rows, key=lambda r: r["median"])
                means.append(f"{arrow(d)}: flights stay typically within {n1(np.median([r['median'] for r in rows]))} NM "
                             f"of the filed route; the largest typical offset, {n1(mx['median'])} NM, is near latitude "
                             f"{mx['lat']:+.1f}°, where controllers commonly give direct routings.")
        D.explain("How far, sideways, aircraft were from the filed route at each point along the journey.",
                  "The horizontal axis follows the route by latitude; the solid line is the typical sideways distance and "
                  "the dashed line the distance exceeded by only one flight in ten.", means)
    D.h2("Efficiency by airspace (FIR)")
    if S["fir"]:
        D.table(["Direction", "FIR", "Flights", "Flown", "Filed", "Flown − filed", "Time (min)"],
                [[arrow(f["dirn"]), f["fir"], str(f["n"]), n1(f["flown_median"]), n1(f["ref_median"]),
                  f"{f['ext_median']:+.1f}" if f["ext_median"] is not None else "–", n1(f["time_median"])] for f in S["fir"]])
        worst = max(S["fir"], key=lambda f: f["ext_median"] or -1e9)
        D.explain("The flown-minus-filed distance inside each flight information region (FIR).",
                  "One row per direction and FIR. The FIR with the largest positive difference adds the most distance.",
                  f"The largest difference is in {worst['fir']} ({arrow(worst['dirn'])}): {worst['ext_median']:+.1f} NM.")
    else:
        D.p("This breakdown needs the FIR boundaries, which are not loaded in CPTEA, so it is not reported for this run. "
            "An administrator can import them under Aeronautical data.")

    # =========================================================== 6. Terminal
    D.h1("6. The arrival phase")
    D.p("The last part of the flight, inside a circle around the destination, is where most waiting happens: aircraft "
        "are sequenced for landing, slowed down, vectored or put into holding patterns. This section measures how long "
        "that part took compared with an unhindered arrival.")
    tb = S["terminal"]["by_ades"]
    if tb:
        D.h2("Time in the last part of the flight")
        D.table(["Arriving at", "Circle", "Flights", "Typical (min)", "Slow flights (min)", "Spread ±", "Unhindered (min)",
                 "Extra (min)", "Holding-type"],
                [[t["ades"], f"{t['ring']} NM", str(t["n"]), n1(t["median"]), n1(t["p85"]), n1(t["var"]), n1(t["unimpeded"]),
                  n1(t["add_mean"]), pc(t["hold_share"])] for t in tb],
                [1.6, 1.2, 1.2, 1.5, 1.7, 1.3, 1.7, 1.3, 1.5],
                "\"Slow flights\" = the time exceeded by 15% of flights. \"Unhindered\" = the time of the quickest arrivals.")
        means = []
        for a2, t in ter_big.items():
            means.append(f"Into {a2}: the last {big} NM took a typical {n1(t['median'])} minutes against "
                         f"{n1(t['unimpeded'])} minutes for an unhindered arrival, so the average flight spent about "
                         f"{n1(t['add_mean'])} minutes extra. {pc(t['hold_share'])} of arrivals made turns typical of "
                         f"holding.")
        if len(ter_big) == 2:
            a1, a2 = sorted(ter_big, key=lambda k: -ter_big[k]["add_mean"])
            means.append(f"Arrivals into {a1} lose more time than arrivals into {a2}.")
        D.explain(
            f"For each destination, how long flights took to cover the last {', '.join(str(int(r)) for r in P['TER-01'])} NM "
            "before landing, and how much longer than an unhindered arrival.",
            "\"Extra\" is the extra time compared with the unhindered arrival: the larger it is, the more waiting happens "
            "near the airport. \"Holding-type\" is the share of arrivals that turned through a full circle or more "
            "near the destination.", means)
    D.insights(ins_for("terminal"))
    if D.img(F.get("ter"), "Extra arrival time and share of arrivals with holding-type turns, by month."):
        tm = S["terminal"]["monthly"]
        means = []
        for a2 in sorted({r["ades"] for r in tm}):
            rows = [r for r in tm if r["ades"] == a2 and r["n"] >= 5]
            if rows:
                hi_ = max(rows, key=lambda r: r["add_mean"])
                means.append(f"Into {a2}: the most extra time was in {hi_['month']} ({n1(hi_['add_mean'])} minutes on "
                             f"average, {pc(hi_['hold_share'])} with holding-type turns).")
        D.explain("The same two arrival measures, month by month.",
                  "Left: average extra minutes in the arrival circle. Right: share of arrivals with holding-type turns. "
                  "Peaks point to busy seasons, weather or temporary restrictions.", means)
    D.insights(ins_for("terminal_monthly"))
    if D.img(F.get("ter_sc"), "Each dot is one flight: en-route time against time in the arrival circle."):
        corr = S["terminal"]["corr"]
        D.explain(
            "Whether flights that took long in the arrival phase had also been slow en route.",
            "Each dot is one flight. If delay were absorbed early, by flying slower en route, dots would rise to the left. "
            "The number r measures the link: near 0 means no link, near −1 means delay is absorbed en route.",
            [f"Into {a2}: r = {r:.2f}, " + ("so there is practically no link: flights arrive at normal speed and wait in "
                                            "the arrival area, which uses more fuel than slowing down earlier would."
                                            if abs(r) < 0.2 else "a noticeable link.") for a2, r in corr.items() if r is not None])
    D.insights(ins_for("terminal_scatter"))

    # =========================================================== 7. Cruise
    D.h1("7. Cruising at the right level")
    D.p("Airlines file the cruise levels they want, because each aircraft burns least fuel at a particular height for "
        "its weight. This section checks, at each waypoint of the flight plan, whether the aircraft was at the level it "
        "had filed for that part of the route.")
    if D.img(F.get("cru"), "Flown level against filed level at the flight-plan waypoints, by direction and aircraft type."):
        best = sorted([c for c in cls if c["n_flights"] >= 10], key=lambda c: -(c["above_1000"] + c["above_2000"]))
        low = sorted([c for c in cls if c["n_flights"] >= 10], key=lambda c: -(c["below_1000"] + c["below_2000"]))
        means = []
        if best:
            c = best[0]
            means.append(f"Flying higher than filed is most common for {arrow(c['dirn'])} {c['type']}: "
                         f"{pc(c['above_1000'] + c['above_2000'])} of waypoints. Higher is often better for fuel late in "
                         f"the flight, as the aircraft gets lighter, so it is reported separately and not counted as a loss.")
        if low and (low[0]["below_1000"] + low[0]["below_2000"]) > 0.02:
            c = low[0]
            means.append(f"Flying lower than filed, which does cost fuel, is most common for {arrow(c['dirn'])} "
                         f"{c['type']}: {pc(c['below_1000'] + c['below_2000'])} of waypoints.")
        D.explain(
            "For every waypoint in the cruise part of the flight plan, whether the aircraft passed it at, above or below "
            "its filed level.",
            "Each bar is one direction and aircraft type (number of flights in brackets). Grey is at the filed level, "
            "blue above, red below; darker shades mean 2,000 ft or more away.", means)
    D.insights(ins_for("cruise"))
    sc = [c for c in S["cruise"]["score"] if c["n"] >= 3]
    if sc:
        D.h2("Cruise level score")
        D.table(["Direction", "Type", "Flights", "Score (typical)", "Score (worst 15%)", "Minutes above", "Minutes below",
                 "Minutes at"],
                [[arrow(c["dirn"]), c["type"], str(c["n"]), n1(c["median"]), n1(c["p85"]), n1(c["time_above_mean"]),
                  n1(c["time_below_mean"]), n1(c["time_at_mean"])] for c in sc],
                [2.4, 1.2, 1.1, 1.5, 1.6, 1.5, 1.5, 1.4],
                f"Score unit: 1,000 ft × minute away from the filed level (penalty mode: {P['CRU-07']}).")
        worst = max(sc, key=lambda c: c["median"] or 0)
        D.explain(
            "A single number per flight for how far, and for how long, the cruise was away from the filed level.",
            "A score of 0 means the whole cruise was flown at the filed levels. 60 means, for example, 1,000 ft away for "
            "an hour, or 2,000 ft away for half an hour. The minutes columns show where the time was spent.",
            [f"The highest typical score is {n1(worst['median'])} for {arrow(worst['dirn'])} {worst['type']}, which spends "
             f"on average {n1(worst['time_above_mean'])} minutes above and {n1(worst['time_below_mean'])} minutes below "
             f"its filed level.",
             "Most of the score comes from flying above the filed level, so it mainly reflects airlines climbing higher "
             "than they filed rather than aircraft being held low." if sum(c["time_above_mean"] for c in sc) >
             sum(c["time_below_mean"] for c in sc) else
             "Most of the score comes from flying below the filed level, a real fuel cost worth looking into."])
    cap = [c for c in S["cruise"]["capped"] if c["n"] >= 3 and c["share"]]
    if cap:
        D.h2("Climbs stopped below the filed level")
        D.table(["Direction", "Type", "Flights", "Capped climbs", "How far below (ft)", "From departure (NM)", "For (min)"],
                [[arrow(c["dirn"]), c["type"], str(c["n"]), pc(c["share"]), n0(c["median_below_ft"]), n0(c["median_dist"]),
                  n1(c["median_min"])] for c in cap], [2.4, 1.2, 1.1, 1.5, 1.7, 1.8, 1.2])
        c = max(cap, key=lambda c: c["share"])
        D.explain(
            "Flights whose first cruise level was lower than the level they filed, usually because air traffic control "
            "kept them lower for traffic.",
            "\"How far below\" is the typical gap to the filed level, \"From departure\" where the capped level started, "
            "and \"For\" how long the aircraft stayed there before climbing further.",
            f"Capped climbs are most frequent for {arrow(c['dirn'])} {c['type']} ({pc(c['share'])} of flights), "
            f"typically {n0(c['median_below_ft'])} ft below the filed level for about {n1(c['median_min'])} minutes.")
    ch = S["cruise"].get("changes") or {}
    if D.img(F.get("chg"), "Where flown cruise level changes happened."):
        D.explain(
            "Every climb or descent made during the cruise, coloured by whether it matches the flight plan.",
            f"Green: the change reached a filed level within {P['CRU-08']:.0f} NM of the waypoint where it was filed. "
            "Amber: the filed level was reached, but elsewhere. Dark: the change was not in the plan at all.",
            [f"{n0(ch.get('n'))} level changes were flown, {pc(ch.get('climb_share'))} of them climbs.",
             f"Only {pc(ch.get('matched_share'))} of the changes to a filed level happened near the waypoint where they were "
             f"filed (typically {n0(ch.get('median_dist_planned'))} NM away), and {pc(ch.get('on_unplanned_share'))} were "
             f"on flights that filed no level change. Level changes are decided tactically, in flight, rather than "
             f"following the plan." if ch else None])
    D.insights(ins_for("cruise_changes"))

    # =========================================================== 8. Level-offs
    pr = S["leveloffs"]["primary"]
    lo_floor = int((pr[0].get("bounds") or [11000])[0] or 11000) if pr else 11000
    D.h1("8. Stopping on the way up and down")
    D.p("The most efficient climb and descent are continuous: the aircraft goes up or down without stopping. A "
        "\"level-off\" is a period of level flight during the climb or descent, usually on instruction from air traffic "
        f"control. Here, level flight means climbing or descending at less than {P['VER-02']:g} feet per second for at least "
        f"{P['VER-03']:.0f} seconds, counted from {lo_floor:,} ft up to the top of climb, and from the top of descent "
        f"down to {lo_floor:,} ft.")
    if pr:
        D.h2("How often flights level off")
        D.table(["Phase", "Airport", "Flights", "With a level-off", "Level-offs per flight", "Typical length (s)",
                 "Level minutes per flight"],
                [[p_["phase"], p_["airport"], str(p_["flights"]), pc(p_["share"]), n1(p_["mean_per_flight"]),
                  n0(p_["median_dur"]), n1(p_["time_per_flight_min"])] for p_ in pr], [1.3, 1.3, 1.2, 1.7, 1.8, 1.7, 1.9])
        worst = max(pr, key=lambda p_: p_["share"])
        D.explain(
            "For climbs out of and descents into each airport: how many flights had to level off, how often and for how long.",
            "\"With a level-off\" is the share of flights with at least one. \"Level minutes per flight\" spreads the "
            "total time spent level over all flights, so it shows the overall cost.",
            [f"Level-offs are most common in the {worst['phase']} {'into' if worst['phase'] == 'descent' else 'out of'} "
             f"{worst['airport']}: {pc(worst['share'])} of flights, {share_words(worst['share'])}."] +
            [f"Climbs out of {p_['airport']} are levelled for {n1(p_['time_per_flight_min'])} minutes per flight on "
             f"average, mostly long level segments near cruise height." for p_ in pr if p_["phase"] == "climb"
             and p_["time_per_flight_min"] and p_["time_per_flight_min"] > 2])
    if D.img(F.get("los"), "Share of flights with a level-off, depending on how long a level segment must last to count."):
        sens = S["leveloffs"]["sensitivity"]
        means = []
        for (ph, a2) in sorted({(s["phase"], s["airport"]) for s in sens}):
            ss = sorted([s for s in sens if s["phase"] == ph and s["airport"] == a2], key=lambda s: s["dur"])
            if len(ss) > 1:
                means.append(f"{ph.capitalize()} {'into' if ph == 'descent' else 'out of'} {a2}: {pc(ss[0]['share'])} of "
                             f"flights at {ss[0]['dur']:.0f} s, falling to {pc(ss[-1]['share'])} at {ss[-1]['dur']:.0f} s.")
        means.append("Because the answer depends so much on this threshold, level-off figures should always be quoted "
                     "with the threshold used.")
        D.explain(
            "How the share of flights with a level-off changes if a level segment must last longer before it is counted.",
            "Each line is one phase and airport. A steep drop means most level-offs are short; a flat line means they "
            "are long, real stops.", means)
    D.insights(ins_for("leveloff_sensitivity"))
    for key, ph in (("loh", "descent"), ("loc", "climb")):
        if D.img(F.get(key), f"Altitudes of {ph} level segments lasting at least 60 seconds."):
            hist = [h for h in S["leveloffs"]["hist"] if h["phase"] == ph]
            means = []
            for h in hist:
                if sum(h["counts"]):
                    k = int(np.argmax(h["counts"]))
                    means.append(f"{ph.capitalize()} {'into' if ph == 'descent' else 'out of'} {h['airport']}: most level "
                                 f"segments are between {h['edges'][k]:,} and {h['edges'][k + 1]:,} ft.")
            D.explain(
                f"At what heights aircraft flew level during the {ph}.",
                "Each bar counts level segments in a 1,000 ft height band. Peaks show the levels where controllers "
                "usually stop traffic, often at the boundary between two control sectors or near a standard arrival level.",
                means)
    D.insights(ins_for("leveloff_hist"))
    bands = [b for b in S["leveloffs"]["bands"] if b["flights"]]
    if bands:
        D.h2("Level time by height band")
        D.table(["Phase", "Airport", "Height band", "Flights", "Typical length (s)", "Typical distance (NM)", "Minutes per flight"],
                [[b["phase"], b["airport"], b["band"], str(b["flights"]), n0(b["median_dur"]), n0(b["median_dist"]),
                  n1(b["min_per_flight"])] for b in bands], [1.2, 1.2, 2.2, 1.1, 1.6, 1.7, 1.6])
        top = max(bands, key=lambda b: b["min_per_flight"])
        D.explain(
            "The same level segments grouped into wider height bands, with the time spent in each.",
            "\"Minutes per flight\" is the time spent level in that band, averaged over all flights in that phase.",
            f"The most level time is spent in the {top['phase']} {'into' if top['phase'] == 'descent' else 'out of'} "
            f"{top['airport']} at {top['band']}: {n1(top['min_per_flight'])} minutes per flight.")

    # =========================================================== 9. Insights
    D.h1("9. All insights and recommendations")
    rest_rev = [i for i in reviewed if i.id not in used and i.kind == "insight"]
    rest_pro = [i for i in proposed if i.id not in used and i.kind == "insight"]
    if reviewed:
        D.p("Insights reviewed and accepted by the analysts, not already shown above:" if rest_rev else
            "All reviewed insights are shown next to the results they describe.")
        D.ul([(i.title + ": " if i.title else "") + (i.edited or i.drafted) for i in rest_rev])
    if rest_pro and not reviewed:
        D.p("Further insights proposed by CPTEA from the numbers, not yet reviewed by an analyst, grouped by kind:")
        by_rule = defaultdict(list)
        for i in rest_pro:
            by_rule[i.rule].append(i)
        for rule in sorted(by_rule, key=lambda r: SUMMARY_ORDER.index(r) if r in SUMMARY_ORDER else 99):
            lst = by_rule[rule]
            D.h2(GROUP_TITLES.get(rule, lst[0].title or rule))
            D.ul(([PLAIN[rule]] if rule in PLAIN else []) +
                 [(i.title + ": " if i.title else "") + (i.edited or i.drafted) for i in lst])
    elif rest_pro:
        D.p(f"CPTEA proposed {len(rest_pro)} further insights that have not been reviewed yet; they are listed on the "
            f"Insights page.")
    recs = [i for i in allins if i.kind == "recommendation" and i.status in ("accepted", "edited", "drafted")]
    if recs:
        D.h2("Recommendations")
        D.ul([(r.title + ": " if r.title else "") + (r.edited or r.drafted) for r in recs])

    # =========================================================== 10. Words
    D.h1("10. Words used in this report")
    D.table(["Word", "Meaning"], [
        ["Callsign", "The flight number used on the radio, for example THA431."],
        ["Filed route", "The route written in the flight plan, from waypoint to waypoint."],
        ["Boundary rings", "Circles around the two airports. Only the part of the flight between them is compared with the filed route."],
        ["Nautical mile (NM)", "1.852 km, the unit of distance in aviation."],
        ["Flight level (FL)", "Height in hundreds of feet: FL370 is 37,000 ft."],
        ["Typical (median)", "The middle value: half the flights are below it, half above."],
        ["Middle 70%", "The range that leaves out the 15% lowest and 15% highest values."],
        ["Holding-type turns", "Turning through a full circle or more near the destination, as in a holding pattern."],
        ["Level-off", "A period of level flight during the climb or the descent."],
        ["Top of climb / descent", "Where the climb ends and where the descent starts."],
        ["Outlier", "A flight that took a clearly different path from the others."],
    ], [1.2, 4])
    title = opts.get("title") or f"Understanding the {A}–{B_} results"
    stamp = (f"Run #{run.id} · dataset {ds.name} v{ds.version} · parameters {ps.name} v{ps.version} · "
             f"CPTEA {run.software_version or config.VERSION}")
    return {"title": title, "subtitle": f"A plain-language guide to analysis run #{run.id}", "blocks": D.blocks,
            "project": project, "run": run, "stamp": stamp, "authors": opts.get("authors") or ""}


# ------------------------------------------------------------------ PDF
def render(c: dict, path: Path, logo: Path | None = None) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (CondPageBreak, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    import matplotlib
    from PIL import Image as PI

    fdir = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    try:
        pdfmetrics.registerFont(TTFont("DV", str(fdir / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DVB", str(fdir / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("DVI", str(fdir / "DejaVuSans-Oblique.ttf")))
        from reportlab.pdfbase.pdfmetrics import registerFontFamily
        registerFontFamily("DV", normal="DV", bold="DVB", italic="DVI", boldItalic="DVB")
        F, FB, FI = "DV", "DVB", "DVI"
    except Exception:  # noqa: BLE001
        F, FB, FI = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
    ink, muted, teal, amber, rule = (colors.HexColor(x) for x in ("#13283A", "#5B6B76", "#0F7482", "#D98E1F", "#D5DEE3"))
    paper, tint, warm = colors.HexColor("#F5F8F9"), colors.HexColor("#EAF3F4"), colors.HexColor("#FBF3E6")
    st = {
        "p": ParagraphStyle("p", fontName=F, fontSize=9.6, leading=14, textColor=ink, spaceAfter=6),
        "h1": ParagraphStyle("h1", fontName=FB, fontSize=15, leading=19, textColor=ink, spaceBefore=6, spaceAfter=8,
                             keepWithNext=1),
        "h2": ParagraphStyle("h2", fontName=FB, fontSize=11.2, leading=14.5, textColor=teal, spaceBefore=8, spaceAfter=5,
                             keepWithNext=1),
        "cap": ParagraphStyle("cap", fontName=FI, fontSize=8.2, leading=10.8, textColor=muted, spaceAfter=6),
        "tc": ParagraphStyle("tc", fontName=F, fontSize=7.6, leading=9.6, textColor=ink),
        "th": ParagraphStyle("th", fontName=FB, fontSize=7.6, leading=9.6, textColor=ink),
        "lbl": ParagraphStyle("lbl", fontName=FB, fontSize=8.4, leading=11.5, textColor=teal),
        "ex": ParagraphStyle("ex", fontName=F, fontSize=9.1, leading=13, textColor=ink),
        "ul": ParagraphStyle("ul", fontName=F, fontSize=9.6, leading=14, textColor=ink, leftIndent=12, bulletIndent=2,
                             spaceAfter=3),
        "title": ParagraphStyle("t", fontName=FB, fontSize=24, leading=29, textColor=colors.white, spaceAfter=8),
        "sub": ParagraphStyle("s", fontName=F, fontSize=12, leading=16, textColor=colors.HexColor("#A9C3CF")),
        "small": ParagraphStyle("sm", fontName=F, fontSize=8.5, leading=12, textColor=colors.HexColor("#A9C3CF")),
        "kf": ParagraphStyle("kf", fontName=F, fontSize=9.8, leading=14, textColor=ink),
    }
    W = A4[0] - 40 * mm

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def box(rows, bg, left=None):
        tb = Table([[r] for r in rows], colWidths=[W])
        style = [("BACKGROUND", (0, 0), (-1, -1), bg), ("LEFTPADDING", (0, 0), (-1, -1), 9),
                 ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5), ("TOPPADDING", (0, 0), (-1, 0), 7),
                 ("BOTTOMPADDING", (0, -1), (-1, -1), 7)]
        if left:
            style.append(("LINEBEFORE", (0, 0), (0, -1), 2.2, left))
        tb.setStyle(TableStyle(style))
        return tb

    def unit(b):
        """The flowables of one block."""
        t = b["t"]
        if t == "p":
            return [Paragraph(esc(b["text"]), st["p"])]
        if t == "ul":
            return [Paragraph(esc(it), st["ul"], bulletText="•") for it in b["items"]] + [Spacer(1, 4)]
        if t == "keyfacts":
            rows = [Paragraph(f"<font color='#D98E1F'>■</font>&nbsp;&nbsp;{esc(x)}", st["kf"]) for x in b["items"]]
            return [box(rows, paper, amber), Spacer(1, 8)] if rows else []
        if t == "explain":
            rows = [Paragraph("What this shows", st["lbl"]), Paragraph(esc(b["what"]), st["ex"]),
                    Paragraph("How to read it", st["lbl"]), Paragraph(esc(b["read"]), st["ex"])]
            if b["means"]:
                rows.append(Paragraph("What it tells us", st["lbl"]))
                rows += [Paragraph(("• " if len(b["means"]) > 1 else "") + esc(m), st["ex"]) for m in b["means"]]
            tb = box(rows, tint, teal)
            return [KeepTogether([tb]) if len(rows) <= 9 else tb, Spacer(1, 10)]
        if t == "insight":
            head = ("Proposed insight (not yet reviewed)" if b["proposed"] else "Insight") + \
                   (f": {b['title']}" if b["title"] else "")
            rows = [Paragraph(esc(head), st["lbl"]), Paragraph(esc(b["text"]), st["ex"])]
            if b.get("plain"):
                rows.append(Paragraph(f"<i>In plain words:</i> {esc(b['plain'])}", st["ex"]))
            return [KeepTogether([box(rows, warm, amber)]), Spacer(1, 8)]
        if t == "table":
            n = len(b["head"])
            ws = b.get("widths")
            cw = [W * w / sum(ws) for w in ws] if ws else [W / n] * n
            data = [[Paragraph(esc(h), st["th"]) for h in b["head"]]] + \
                   [[Paragraph(esc(x), st["tc"]) for x in r] for r in b["rows"]]
            tb = Table(data, colWidths=cw, repeatRows=1)
            tb.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), .4, rule),
                                    ("BACKGROUND", (0, 0), (-1, 0), paper), ("LINEBELOW", (0, 0), (-1, 0), .9, teal),
                                    ("TOPPADDING", (0, 0), (-1, -1), 2.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
                                    ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
            cap = [Paragraph(esc(b["caption"]), st["cap"])] if b.get("caption") else [Spacer(1, 6)]
            # short tables stay in one piece with their note; long ones may continue on the next page (header repeats)
            return [KeepTogether([tb] + cap)] if len(b["rows"]) <= 18 else [tb] + cap
        if t == "img":
            im = PI.open(b["path"])
            w = W
            h = w * im.size[1] / im.size[0]
            if h > 120 * mm:
                h = 120 * mm
                w = h * im.size[0] / im.size[1]
            return [KeepTogether([Image(b["path"], width=w, height=h), Paragraph(esc(b["caption"]), st["cap"])])]
        return []

    # Headings are never left at the bottom of a page: each heading (and any heading right after it) is bound to the
    # first piece of content that follows, and a section heading also needs a minimum of free space below it.
    story, pending, need = [], [], 0
    for b in c["blocks"]:
        if b["t"] == "h":
            pending.append(Paragraph(esc(b["text"]), st["h1" if b["level"] == 1 else "h2"]))
            need = max(need, 60 if b["level"] == 1 else 42)
            continue
        flows = unit(b)
        if not flows:
            continue
        if pending:
            story += [CondPageBreak(need * mm), KeepTogether(pending + [flows[0]])] + flows[1:]
            pending, need = [], 0
        else:
            story += flows
    if pending:
        story += pending

    run, proj = c["run"], c["project"]
    today = datetime.now(timezone.utc)

    def cover(cv, doc):
        cv.saveState()
        cv.setFillColor(ink)
        cv.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
        # route ribbon, as in the application header
        y = A4[1] - 92 * mm
        x0, x1 = 26 * mm, A4[0] - 26 * mm
        cv.setFont(FB, 26)
        cv.setFillColor(colors.white)
        cv.drawString(x0, y - 4 * mm, proj.adep)
        cv.drawRightString(x1, y - 4 * mm, proj.ades)
        a = x0 + pdfmetrics.stringWidth(proj.adep, FB, 26) + 10 * mm
        b_ = x1 - pdfmetrics.stringWidth(proj.ades, FB, 26) - 10 * mm
        cv.setStrokeColor(amber)
        cv.setLineWidth(2.2)
        p = cv.beginPath()
        p.moveTo(a, y)
        p.curveTo(a + (b_ - a) * .3, y + 7 * mm, a + (b_ - a) * .7, y - 7 * mm, b_, y)
        cv.drawPath(p, stroke=1, fill=0)
        cv.setStrokeColor(colors.HexColor("#5FB3BE"))
        cv.setLineWidth(.8)
        cv.setDash(2, 2)
        for cx in (a, b_):
            cv.circle(cx, y, 4 * mm, stroke=1, fill=0)
        cv.setDash()
        cv.setFillColor(colors.white)
        for cx in (a, b_):
            cv.circle(cx, y, 1.3 * mm, stroke=0, fill=1)
        cv.setFillColor(colors.HexColor("#A9C3CF"))
        cv.setFont(F, 8.5)
        cv.drawString(x0, 22 * mm, c["stamp"])
        cv.drawString(x0, 17 * mm, f"Generated {today:%d %B %Y} · CPTEA City-Pair Trajectory Efficiency Analyser")
        cv.restoreState()

    def foot(cv, doc):
        cv.saveState()
        cv.setFont(F, 7.5)
        cv.setFillColor(muted)
        cv.drawString(20 * mm, 11 * mm, f"{c['title']} · run #{run.id}")
        cv.drawRightString(A4[0] - 20 * mm, 11 * mm, str(doc.page))
        cv.setStrokeColor(rule)
        cv.setLineWidth(.5)
        cv.line(20 * mm, 14 * mm, A4[0] - 20 * mm, 14 * mm)
        cv.restoreState()

    from reportlab.platypus import Frame, NextPageTemplate, PageBreak, PageTemplate, BaseDocTemplate
    doc = BaseDocTemplate(str(path), pagesize=A4, title=c["title"], author=c["authors"] or "CPTEA",
                          leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=20 * mm)
    body = Frame(20 * mm, 20 * mm, W, A4[1] - 38 * mm, id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    cov_frame = Frame(26 * mm, 40 * mm, A4[0] - 52 * mm, A4[1] - 140 * mm, id="cover", leftPadding=0, rightPadding=0)
    doc.addPageTemplates([PageTemplate("cover", [cov_frame], onPage=cover), PageTemplate("body", [body], onPage=foot)])
    head = [Paragraph(esc(c["title"]), st["title"]), Paragraph(esc(c["subtitle"]), st["sub"])]
    if c["authors"]:
        head += [Spacer(1, 6), Paragraph(esc(c["authors"]), st["sub"])]
    if logo and Path(logo).exists():
        head = [Image(str(logo), width=34 * mm, height=17 * mm, kind="proportional"), Spacer(1, 10)] + head
    doc.build(head + [NextPageTemplate("body"), PageBreak()] + story)
    return path


def generate(db, report, run: Run) -> dict:
    out_dir = config.run_dir(run.id) / f"report_{report.id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = report.options or {}
    c = build(db, run, opts, out_dir)
    logo = Path(opts["logo_path"]) if opts.get("logo_path") else None
    base = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in c["title"])[:60] or "report"
    return {"pdf": str(render(c, out_dir / f"{base}_explained.pdf", logo))}
