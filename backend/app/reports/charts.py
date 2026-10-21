"""Report charts rendered with matplotlib from the run summary (same data as the Accumulated view, FR-REP-04)."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

C1, C2, C3, C4, GRID, INK, MUTED = "#0F7482", "#E39B2D", "#5FB3BE", "#C4501F", "#E3E8EA", "#1B1F24", "#6B7A82"
SERIES = [C1, C2, C3, C4, "#6250d6", "#008300", "#e87ba4", "#e34948"]

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": "#9a9993", "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": .6, "axes.titleweight": "bold",
                     "axes.titlesize": 10, "legend.frameon": False, "axes.axisbelow": True})


def _save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def track_map(tracks, routes, airports, path):
    dirs = sorted({t["dirn"] for t in tracks})
    fig, axs = plt.subplots(1, max(1, len(dirs)), figsize=(4.4 * max(1, len(dirs)), 5.4), squeeze=False)
    for ax, d in zip(axs[0], dirs):
        tt = [t for t in tracks if t["dirn"] == d]
        for t in tt:
            c = np.array(t["coords"])
            ax.plot(c[:, 1], c[:, 0], lw=.3, alpha=.3, color=C4 if t["outlier"] else C1)
        for name, coords in routes.get(d, []):
            c = np.array(coords)
            ax.plot(c[:, 1], c[:, 0], color=C2, lw=1.8)
        for ap, (la, lo) in airports.items():
            ax.plot(lo, la, "o", color=INK, ms=4)
            ax.annotate(ap, (lo, la), xytext=(4, 4), textcoords="offset points", fontsize=8)
        ax.set_title(f"{d}: {len(tt)} tracks")
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_xlabel("Longitude (°)")
    axs[0][0].set_ylabel("Latitude (°)")
    axs[0][0].plot([], [], color=C1, label="Tracks")
    axs[0][0].plot([], [], color=C4, label="Outliers")
    axs[0][0].plot([], [], color=C2, lw=1.8, label="Most frequent filed route")
    axs[0][0].legend(loc="lower left", fontsize=7)
    return _save(fig, path)


def extension_rings(S, path):
    ext = S["extension"]
    dirs = S["dirs"]
    rings = sorted({e["ring"] for e in ext})
    fig, ax = plt.subplots(figsize=(7, 0.8 + 0.9 * len(rings)))
    for k, R in enumerate(rings):
        for j, d in enumerate(dirs):
            e = next((x for x in ext if x["ring"] == R and x["dirn"] == d), None)
            if not e:
                continue
            y = k + (j - (len(dirs) - 1) / 2) * 0.3
            c = SERIES[j % len(SERIES)]
            ax.plot([e["p15"], e["p85"]], [y, y], color=c, lw=2, solid_capstyle="round")
            ax.plot(e["median"], y, "o", color=c, ms=7, mec="white", mew=1.5, label=d if k == 0 else None)
            ax.annotate(f"{e['median']:+.1f}", (e["median"], y), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=7.5)
    ax.axvline(0, color=INK, lw=.8)
    ax.set_yticks(range(len(rings)))
    ax.set_yticklabels([f"{r} NM rings" for r in rings])
    ax.set_xlabel("Flown minus filed-route distance (NM) · dot = median, bar = P15–P85")
    ax.set_title("Path extension against the filed route, by en-route boundary")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(axis="y", visible=False)
    return _save(fig, path)


def enroute_monthly(S, path):
    tm = S["enroute_monthly"]
    fig, ax = plt.subplots(figsize=(7, 3))
    for j, d in enumerate(S["dirs"]):
        rows = sorted([r for r in tm if r["dirn"] == d], key=lambda r: r["month"])
        if not rows:
            continue
        x = np.arange(len(rows))
        c = SERIES[j % len(SERIES)]
        ax.fill_between(x, [r["p15"] for r in rows], [r["p85"] for r in rows], color=c, alpha=.15)
        ax.plot(x, [r["median"] for r in rows], color=c, lw=2, marker="o", ms=3, label=d)
        ax.set_xticks(x)
        ax.set_xticklabels([r["month"][2:] for r in rows], rotation=60, fontsize=7)
    ax.set_ylabel(f"En-route time, {S['main_ring']} NM rings (min)")
    ax.set_title("En-route time by month · median with P15–P85 band")
    ax.legend(fontsize=8)
    return _save(fig, path)


def lateral(S, path):
    fig, ax = plt.subplots(figsize=(7, 3))
    for j, d in enumerate(S["dirs"]):
        rows = sorted([r for r in S["lateral"] if r["dirn"] == d], key=lambda r: r["lat"])
        if not rows:
            continue
        c = SERIES[j % len(SERIES)]
        ax.plot([r["lat"] for r in rows], [r["median"] for r in rows], color=c, lw=2, label=f"{d} median")
        ax.plot([r["lat"] for r in rows], [r["p90"] for r in rows], color=c, lw=1.2, ls="--", label=f"{d} P90")
    ax.set_xlabel("Latitude (°)")
    ax.set_ylabel("Distance from filed route (NM)")
    ax.set_title("Lateral deviation from the filed route")
    ax.legend(fontsize=7, ncol=2)
    return _save(fig, path)


def terminal_monthly(S, path):
    tm = S["terminal"]["monthly"]
    aps = sorted({r["ades"] for r in tm})
    fig, axs = plt.subplots(1, 2, figsize=(8, 3))
    months = sorted({r["month"] for r in tm})
    w = 0.8 / max(1, len(aps))
    for j, ap in enumerate(aps):
        rows = {r["month"]: r for r in tm if r["ades"] == ap}
        x = np.arange(len(months)) + (j - (len(aps) - 1) / 2) * w
        axs[0].bar(x, [rows[m]["add_mean"] if m in rows else 0 for m in months], width=w, color=SERIES[j % 8], label=f"into {ap}")
        axs[1].bar(x, [100 * rows[m]["hold_share"] if m in rows else 0 for m in months], width=w, color=SERIES[j % 8], label=f"into {ap}")
    for ax in axs:
        ax.set_xticks(range(len(months)))
        ax.set_xticklabels([m[2:] for m in months], rotation=60, fontsize=7)
        ax.grid(axis="x", visible=False)
        ax.legend(fontsize=7)
    axs[0].set_title(f"Extra time in last {S['terminal']['big_ring']} NM (min)")
    axs[1].set_title("Arrivals with holding-type turns (%)")
    return _save(fig, path)


def cruise_classes(S, path):
    cls = [c for c in S["cruise"]["classes"] if c["n_flights"] >= 5]
    if not cls:
        return None
    cats = ["below_2000", "below_1000", "at", "above_1000", "above_2000"]
    labels = ["≥2,000 ft below", "1,000 ft below", "At filed", "1,000 ft above", "≥2,000 ft above"]
    cols = ["#c4501f", "#f2a17c", "#cfcec8", "#8cb8ec", "#1f5fad"]
    fig, ax = plt.subplots(figsize=(7.5, 0.6 + 0.42 * len(cls)))
    left = np.zeros(len(cls))
    for c, lab, col in zip(cats, labels, cols):
        v = np.array([100 * x[c] for x in cls])
        ax.barh(range(len(cls)), v, left=left, color=col, edgecolor="white", height=.6, label=lab)
        for i, (l, ww) in enumerate(zip(left, v)):
            if ww >= 8:
                ax.text(l + ww / 2, i, f"{ww:.0f}%", ha="center", va="center", fontsize=7,
                        color="white" if col in ("#c4501f", "#1f5fad") else INK)
        left += v
    ax.set_yticks(range(len(cls)))
    ax.set_yticklabels([f"{x['dirn']} · {x['type']} ({x['n_flights']})" for x in cls], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of cruise waypoints (altitude at closest approach vs filed level)")
    ax.legend(ncol=5, fontsize=6.5, loc="upper center", bbox_to_anchor=(0.5, -0.25))
    ax.grid(False)
    ax.set_title("Flown cruise level against the filed level")
    return _save(fig, path)


def leveloff_sensitivity(S, path):
    sens = S["leveloffs"]["sensitivity"]
    keys = sorted({(s["phase"], s["airport"]) for s in sens})
    fig, ax = plt.subplots(figsize=(7, 3.2))
    for j, (ph, ap) in enumerate(keys):
        rows = sorted([s for s in sens if s["phase"] == ph and s["airport"] == ap], key=lambda s: s["dur"])
        ax.plot([s["dur"] for s in rows], [100 * s["share"] for s in rows], marker="o", ms=3, lw=2,
                color=SERIES[j % 8], ls="-" if ph == "descent" else "--",
                label=f"{'Descent into' if ph == 'descent' else 'Climb out of'} {ap}")
    ax.set_xlabel("Minimum level-segment duration counted (s)")
    ax.set_ylabel("% of flights with ≥ 1 level-off")
    ax.set_title("Level-off share by duration threshold")
    ax.legend(fontsize=7)
    return _save(fig, path)


def leveloff_hist(S, path, phase="descent"):
    hs = [h for h in S["leveloffs"]["hist"] if h["phase"] == phase]
    if not hs:
        return None
    fig, axs = plt.subplots(1, len(hs), figsize=(4 * len(hs), 2.8), squeeze=False)
    for ax, h in zip(axs[0], hs):
        e = np.array(h["edges"]) / 1000
        ax.bar(e + .5, h["counts"], width=.8, color=C1)
        ax.set_title(f"{'Descent into' if phase == 'descent' else 'Climb out of'} {h['airport']}")
        ax.set_xlabel("Altitude (thousand ft)")
        ax.grid(axis="x", visible=False)
    axs[0][0].set_ylabel("Level segments ≥ 60 s")
    return _save(fig, path)


def cluster_monthly(S, path):
    cm = S.get("clusters", {}).get("monthly", [])
    if not cm:
        return None
    dirs = sorted({r["dirn"] for r in cm})
    fig, axs = plt.subplots(1, len(dirs), figsize=(4 * len(dirs), 2.8), squeeze=False)
    for ax, d in zip(axs[0], dirs):
        rows = [r for r in cm if r["dirn"] == d]
        months = sorted({r["month"] for r in rows})
        cls = sorted({r["cluster"] for r in rows})
        bottom = np.zeros(len(months))
        for j, c in enumerate(cls):
            v = np.array([sum(r["n"] for r in rows if r["month"] == m and r["cluster"] == c) for m in months])
            ax.bar(range(len(months)), v, bottom=bottom, color=SERIES[j % 8], label=f"Cluster {c}")
            bottom += v
        ax.set_xticks(range(len(months)))
        ax.set_xticklabels([m[2:] for m in months], rotation=60, fontsize=7)
        ax.set_title(f"Route usage, {d}")
        ax.legend(fontsize=7)
        ax.grid(axis="x", visible=False)
    return _save(fig, path)


def extension_by_type(S, path):
    """Box plot of path extension by direction and aircraft type (the Results 'By aircraft type' tab)."""
    rows = [b for b in S["extension_box"] if b["by"] == "dirn+type" and b["n"] >= 3]
    if not rows:
        return None
    rows.sort(key=lambda b: b["key"])
    dirs = sorted({b["key"].split(" · ")[0] for b in rows})
    fig, ax = plt.subplots(figsize=(7.6, 0.6 + 0.32 * len(rows)))
    for i, b in enumerate(rows):
        d = next((k for k, x in enumerate(dirs) if b["key"].startswith(x)), 0)
        c = SERIES[d % len(SERIES)]
        ax.plot([b["min"], b["max"]], [i, i], color=c, lw=.8, alpha=.6)
        ax.add_patch(plt.Rectangle((b["p25"], i - .3), b["p75"] - b["p25"], .6, color=c, alpha=.35, lw=0))
        ax.plot([b["median"], b["median"]], [i - .3, i + .3], color=c, lw=2)
    ax.axvline(0, color=INK, lw=.8)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([f"{b['key']} ({b['n']})" for b in rows], fontsize=7.5)
    ax.set_xlabel(f"Flown minus filed-route distance between the {S['main_ring']} NM rings (NM)")
    ax.set_title("Path extension by aircraft type · box = middle half of flights, line = median")
    ax.grid(axis="y", visible=False)
    return _save(fig, path)


def terminal_scatter(S, path):
    """En-route time against time in the last arrival ring, one dot per flight."""
    pts = S["terminal"]["scatter"]
    if not pts:
        return None
    aps = sorted({p["ades"] for p in pts})
    fig, ax = plt.subplots(figsize=(7, 3.6))
    for j, ap in enumerate(aps):
        xs = [p["enroute"] for p in pts if p["ades"] == ap and p["enroute"] is not None and p["arrival"] is not None]
        ys = [p["arrival"] for p in pts if p["ades"] == ap and p["enroute"] is not None and p["arrival"] is not None]
        r = S["terminal"]["corr"].get(ap)
        ax.scatter(xs, ys, s=6, alpha=.55, color=SERIES[j % 8], lw=0,
                   label=f"into {ap}" + (f" (r = {r:.2f})" if r is not None else ""))
    ax.set_xlabel(f"En-route time between the {S['main_ring']} NM rings (min)")
    ax.set_ylabel(f"Time in last {S['terminal']['big_ring']} NM (min)")
    ax.set_title("Does a slow arrival follow a slow en-route phase?")
    ax.legend(fontsize=7.5, markerscale=2)
    return _save(fig, path)


def cruise_changes_map(S, tracks, path):
    """Where flown cruise level changes happen, against the plan."""
    pts = (S["cruise"].get("changes") or {}).get("points") or []
    if not pts:
        return None
    fig, ax = plt.subplots(figsize=(5.2, 5.6))
    for t in tracks[:400]:
        c = np.array(t["coords"])
        ax.plot(c[:, 1], c[:, 0], lw=.25, alpha=.15, color=MUTED)
    groups = [("at the filed waypoint", lambda p: p.get("matched"), "#1D7A46"),
              ("filed level, reached elsewhere", lambda p: p.get("reaches_planned") and not p.get("matched"), C2),
              ("not in the flight plan", lambda p: not p.get("reaches_planned"), INK)]
    for label, fn, col in groups:
        g = [p for p in pts if fn(p)]
        if g:
            ax.scatter([p["lon"] for p in g], [p["lat"] for p in g], s=9, color=col, alpha=.75, lw=0, label=f"{label} ({len(g)})")
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("Longitude (°)")
    ax.set_ylabel("Latitude (°)")
    ax.set_title("Flown cruise level changes")
    ax.legend(fontsize=7, loc="lower left")
    return _save(fig, path)
