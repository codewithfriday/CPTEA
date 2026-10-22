"""Create CPTEA projects for city pairs recorded in the NavTrace database.

For each pair this command
  1. creates the project (or reuses the one with the same two aerodromes), with the aerodrome reference points
     from the AIS data (NavEarth) or the table below,
  2. saves a parameter version adapted to the pair's geometry (see adapt_params),
  3. loads the tracks from NavTrace into a new dataset version,
  4. imports the flight plans retrieved from the AirNav flight data API (fetch_city_pair_fpls.py output),
  5. optionally starts an analysis run.

Run it on the CPTEA server, from the backend folder, with the same environment as the application:

    cd backend
    python -m app.tools.add_city_pairs --fpl-dir /path/to/fpl_city_pairs --run

It never changes an existing dataset or run. VTBS-WADD keeps its FR24 data; only its aerodrome reference points are
updated to the AIP values (--keep-vtbs-wadd-coords leaves them as they are).
"""
import argparse
import math
import sys
import time
from pathlib import Path

from .. import config
from ..db import SessionLocal
from ..defaults import DEFAULT_VALUES, validate
from ..engine import ingest
from ..engine.geo import gc
from ..models import Dataset, Flight, ParameterSet, Project, Run, User, now

BEST_PAIRS = ["WADD-WIII", "WIII-WSSS", "WARR-WIII", "WIII-WIMM", "WAAA-WIII", "WADD-WSSS"]

# Aerodrome reference points (decimal degrees). Indonesian ARPs from AIP Vol II (NavEarth arp_vol_ii);
# VTBS and WSSS as provided by AirNav. Used when the AIS data in CPTEA does not hold the aerodrome.
ARP = {
    "WIII": (-6.123611, 106.661111, "Soekarno-Hatta, Jakarta"),
    "WADD": (-8.7475, 115.169167, "I Gusti Ngurah Rai, Bali"),
    "WARR": (-7.380833, 112.786389, "Juanda, Surabaya"),
    "WIMM": (3.642222, 98.885278, "Kualanamu, Medan"),
    "WAAA": (-5.060833, 119.554444, "Sultan Hasanuddin, Makassar"),
    "WIDD": (1.118889, 104.113889, "Hang Nadim, Batam"),
    "WSSS": (1.359211, 103.989325, "Changi, Singapore"),
    "VTBS": (13.685833, 100.748889, "Suvarnabhumi, Bangkok"),
}


def log(*a):
    print(*a, flush=True)


def arp(db, code):
    """AIS (NavEarth) aerodrome reference point first, then the table above."""
    from ..models import Fix
    f = db.query(Fix).filter_by(name=code, kind="aerodrome").order_by(Fix.source.desc()).first()
    if f:
        return f.lat, f.lon, ARP.get(code, (0, 0, code))[2], "AIS"
    if code in ARP:
        return ARP[code][0], ARP[code][1], ARP[code][2], "table"
    raise SystemExit(f"No reference point for {code}: refresh NavEarth or add it to ARP in {__file__}")


def _unused_intermediate(lat1, lon1, lat2, lon2, f):
    """Point at fraction f along the great circle."""
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = 2 * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    a, b = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
    x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
    y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
    z = a * math.sin(p1) + b * math.sin(p2)
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


def adapt_params(base: dict, a, b) -> tuple[dict, list[str]]:
    """The VTBS-WADD defaults, adapted to this pair's geometry:
    - GEN-03 minimum flight duration: 60% of an estimated block time (the VTBS-WADD 150 min would exclude every
      flight of a shorter pair);
    - CLU-02/CLU-02b cut lines: latitudes for a mainly north-south pair, longitudes for an east-west one, spread
      evenly along the route outside the classification clip radius (CLU-01) around both aerodromes;
    - HOR-01b boundary rings: only rings that leave at least 50 NM of en-route flight between them."""
    v = dict(base)
    notes = []
    d = float(gc(a[0], a[1], b[0], b[1]))
    est_block = d / 430 * 60 + 15
    v["GEN-03"] = max(20, int(0.6 * est_block // 5 * 5))
    notes.append(f"GEN-03 {base['GEN-03']} → {v['GEN-03']} min (60% of an estimated {est_block:.0f} min block time)")
    dlat = abs(a[0] - b[0])
    dlon = abs(a[1] - b[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    orient = "latitude" if dlat >= dlon else "longitude"
    # every cut line must lie outside the clip radius (CLU-01) around both aerodromes, measured across the line
    # (a latitude line is |Δlat| × 60 NM from an aerodrome; a meridian |Δlon| × 60 × cos(lat) NM)
    clip = float(v["CLU-01"]) + 15
    i = 0 if orient == "latitude" else 1
    ca, cb = a[i], b[i]
    margin_a = clip / 60 / (1 if i == 0 else math.cos(math.radians(a[0])))
    margin_b = clip / 60 / (1 if i == 0 else math.cos(math.radians(b[0])))
    lo_, hi_ = (ca + margin_a, cb - margin_b) if ca < cb else (cb + margin_b, ca - margin_a)
    if hi_ <= lo_:
        raise ValueError("the aerodromes are too close for cut lines outside the clip radius; lower CLU-01")
    span_nm = (hi_ - lo_) * 60 * (1 if i == 0 else math.cos(math.radians((a[0] + b[0]) / 2)))
    n = max(3, min(7, int(span_nm / 80) + 1))
    cuts = sorted({round(lo_ + (hi_ - lo_) * k / (n - 1), 1) for k in range(n)}, reverse=True)
    v["CLU-02"], v["CLU-02b"] = cuts, orient
    notes.append(f"CLU-02 cut lines → {orient}s {', '.join(f'{c:g}°' for c in cuts)}")
    rings = [r for r in base["HOR-01b"] if 2 * float(r) + 50 <= d]
    if rings != list(base["HOR-01b"]):
        v["HOR-01b"] = rings
        notes.append(f"HOR-01b rings → {rings} NM (the route is {d:.0f} NM)")
    if float(v["HOR-01"]) not in [float(r) for r in rings]:
        v["HOR-01"] = max(r for r in rings)
        notes.append(f"HOR-01 en-route boundary → {v['HOR-01']} NM")
    return v, notes


def wait(db, obj, attr="status", done=("ready", "done", "error"), label=""):
    while True:
        db.refresh(obj)
        if getattr(obj, attr) in done:
            return getattr(obj, attr)
        time.sleep(3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", default=",".join(BEST_PAIRS), help="comma-separated city pairs, e.g. WADD-WIII,WIII-WSSS")
    ap.add_argument("--fpl-dir", help="folder written by fetch_city_pair_fpls.py (fpl_<ADEP>_<ADES>.json files)")
    ap.add_argument("--from", dest="d0", help="first date of flight to load (YYYY-MM-DD)")
    ap.add_argument("--to", dest="d1", help="last date of flight to load (YYYY-MM-DD)")
    ap.add_argument("--owner", help="username that owns the projects (default: the first administrator)")
    ap.add_argument("--run", action="store_true", help="start an analysis run for each project when its data is loaded")
    ap.add_argument("--keep-vtbs-wadd-coords", action="store_true", help="do not update the VTBS-WADD reference points")
    a = ap.parse_args()
    from ..main import bootstrap
    bootstrap()
    if not config.NAVTRACE_DSN:
        sys.exit("Set CPTEA_NAVTRACE_DSN (the NavTrace database connection) in the CPTEA environment first.")
    st = ingest.navtrace_status()
    if not st["ok"]:
        sys.exit(f"NavTrace database not reachable: {st['message']}")
    db = SessionLocal()
    owner = db.query(User).filter_by(username=a.owner).first() if a.owner else \
        db.query(User).filter_by(role="admin", active=True).order_by(User.id).first()
    if not owner:
        sys.exit("Owner user not found")
    system = db.query(ParameterSet).filter(ParameterSet.project_id.is_(None)).order_by(ParameterSet.id.desc()).first()
    base = dict(system.values if system else DEFAULT_VALUES)
    summary = []

    # ---- VTBS-WADD keeps its FR24 data; reference points to the AIP values
    if not a.keep_vtbs_wadd_coords:
        for p in db.query(Project).filter(Project.adep.in_(["VTBS", "WADD"]), Project.ades.in_(["VTBS", "WADD"])).all():
            new = {p.adep: arp(db, p.adep), p.ades: arp(db, p.ades)}
            old = ((p.adep_lat, p.adep_lon), (p.ades_lat, p.ades_lon))
            p.adep_lat, p.adep_lon = new[p.adep][:2]
            p.ades_lat, p.ades_lon = new[p.ades][:2]
            db.commit()
            moved = [gc(o[0], o[1], n[0], n[1]) for o, n in zip(old, ((p.adep_lat, p.adep_lon), (p.ades_lat, p.ades_lon)))]
            log(f"{p.name}: reference points updated ({p.adep} moved {moved[0]:.2f} NM, {p.ades} {moved[1]:.2f} NM); "
                f"existing runs keep their results, start a new run to use them")

    for pair in [x.strip().upper().replace("–", "-") for x in a.pairs.split(",") if x.strip()]:
        A, B = pair.split("-")
        pa, pb = arp(db, A), arp(db, B)
        log(f"\n== {A}–{B}  ({pa[2]} – {pb[2]})")
        p = db.query(Project).filter(((Project.adep == A) & (Project.ades == B)) | ((Project.adep == B) & (Project.ades == A))).first()
        if p:
            log(f"   using existing project #{p.id} {p.name}")
        else:
            p = Project(name=f"{A}–{B}", adep=A, ades=B, adep_lat=pa[0], adep_lon=pa[1], ades_lat=pb[0], ades_lon=pb[1],
                        description=f"{pa[2]} – {pb[2]}. Tracks: NavTrace ADS-B database. Flight plans: AirNav flight data API.",
                        callsigns={}, owner_id=owner.id)
            db.add(p)
            db.commit()
            log(f"   created project #{p.id} (ARP from {pa[3]}/{pb[3]})")
        vals, notes = adapt_params(base, (p.adep_lat, p.adep_lon), (p.ades_lat, p.ades_lon))
        vals = validate(vals, True, base)
        latest = db.query(ParameterSet).filter_by(project_id=p.id).order_by(ParameterSet.id.desc()).first()
        if latest and latest.values == vals:
            ps = latest
        else:
            name = f"{p.name} parameters"
            ver = (latest.version + 1) if latest and latest.name == name else 1
            ps = ParameterSet(project_id=p.id, name=name, version=ver, values=vals, parent_id=system.id if system else None,
                              author_id=owner.id, note="Adapted from the system defaults to this pair: " + "; ".join(notes))
            db.add(ps)
            db.commit()
        for n_ in notes:
            log(f"   {n_}")
        # tracks
        pv = ingest.navtrace_status(p.adep, p.ades, a.d0, a.d1)
        log(f"   NavTrace: {pv.get('flights')} flights {pv.get('by_direction')} ({pv.get('first')} to {pv.get('last')})")
        ver = max([d.version for d in db.query(Dataset).filter_by(project_id=p.id).all()] or [0]) + 1
        ds = Dataset(project_id=p.id, version=ver, name=f"NavTrace {a.d0 or pv.get('first')} to {a.d1 or pv.get('last')}",
                     created_by=owner.id, status="loading", message="Queued")
        db.add(ds)
        db.commit()
        t0 = time.time()
        ingest.load_tracks_navtrace(ds.id, vals, a.d0, a.d1)
        db.refresh(ds)
        if ds.status != "ready":
            log(f"   tracks: FAILED: {ds.message}")
            summary.append((pair, p.id, "tracks failed", ds.message))
            continue
        inc = db.query(Flight).filter_by(dataset_id=ds.id, included=True).count()
        log(f"   tracks: {ds.message}; {inc} included ({time.time() - t0:.0f} s)")
        # flight plans
        fp = None
        if a.fpl_dir:
            for cand in (f"fpl_{A}_{B}.json", f"fpl_{B}_{A}.json"):
                if (Path(a.fpl_dir) / cand).exists():
                    fp = Path(a.fpl_dir) / cand
            if fp:
                r = ingest.import_fpl_file(db, p.id, fp)
                log(f"   flight plans: {r['added']} added, {r['duplicates']} already loaded, {r['errors']} unreadable ({fp.name})")
            else:
                log(f"   flight plans: no fpl_{A}_{B}.json in {a.fpl_dir}")
        run_id = None
        if a.run:
            from ..engine import pipeline
            run = Run(project_id=p.id, dataset_id=ds.id, paramset_id=ps.id, user_id=owner.id,
                      name=f"{p.name}: NavTrace, adapted parameters", status="queued", message="Queued")
            db.add(run)
            db.commit()
            t0 = time.time()
            pipeline.execute(run.id)
            db.refresh(run)
            run_id = run.id
            s = run.summary or {}
            log(f"   run #{run.id}: {run.status} ({time.time() - t0:.0f} s) {run.message}")
            if run.status == "done":
                import pandas as pd
                res = pd.read_parquet(config.run_dir(run.id) / "flights.parquet")
                share = float(res.outlier.mean()) if len(res) else 0
                log(f"   {len(res)} flights analysed, {int(res.outlier.sum())} outliers ({100 * share:.1f}%), "
                    f"{int(res.has_ref.sum())} with a reference route")
                if share > 0.05:
                    log("   WARNING: more than 5% outliers: check the cut lines (Settings) before using the results")
        summary.append((pair, p.id, f"dataset v{ds.version}: {ds.n_flights} flights, {inc} included", f"run #{run_id}" if run_id else ""))
    log("\nSummary")
    for row in summary:
        log("   " + " · ".join(str(x) for x in row if x != ""))
    db.close()


if __name__ == "__main__":
    main()
