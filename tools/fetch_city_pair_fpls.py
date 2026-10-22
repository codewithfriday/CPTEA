#!/usr/bin/env python3
"""Fetch the flight plans of every NavTrace flight in the CPTEA candidate city pairs.

1. Lists the flights of each city pair (both directions) from navtrace.flight_trajectory.
2. Asks the AirNav flight data API for each callsign and date of flight
   (http://172.20.17.117:3000/api/flights?callsign=...&dof=...).
3. Keeps the plan whose aerodromes match the flight and whose EOBT is nearest the first ADS-B time.
4. Writes, per city pair, a JSON file that CPTEA imports as-is (Data > Flight plans > Import a file),
   plus a coverage summary and the list of flights without a plan.

Run it on a machine on the AirNav network (it needs both 172.20.25.30:5432 and 172.20.17.117:3000).
Only the Python standard library is needed, plus a PostgreSQL driver for step 1:
    pip install "psycopg[binary]"        (or psycopg2-binary)
or skip the driver and give it a CSV exported with psql (see --flights-csv).

Examples
    export PGPASSWORD=...                      # or use ~/.pgpass
    python3 fetch_city_pair_fpls.py --db "host=172.20.25.30 dbname=navtrace_flight user=readonly"
    python3 fetch_city_pair_fpls.py --flights-csv flights.csv          # list exported with psql
    python3 fetch_city_pair_fpls.py --db "..." --pairs WADD-WIII,WIII-WSSS --from 2026-09-01

Interrupted runs resume: API answers are cached in OUT/raw/ and are not requested again.
"""
import argparse
import csv
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_PAIRS = ["WADD-WIII", "WIII-WSSS", "WARR-WIII", "WIII-WIMM", "WAAA-WIII", "WADD-WSSS"]

FLIGHTS_SQL = """
SELECT gufi, callsign, dof::text AS dof, adep, ades,
       to_char(first_seen AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') AS first_seen_utc
FROM navtrace.flight_trajectory
WHERE ((adep = %(a)s AND ades = %(b)s) OR (adep = %(b)s AND ades = %(a)s))
  AND callsign IS NOT NULL AND callsign <> ''
  AND (%(d0)s::date IS NULL OR dof >= %(d0)s::date)
  AND (%(d1)s::date IS NULL OR dof <= %(d1)s::date)
ORDER BY dof, callsign
"""


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ step 1: flights
def flights_from_db(dsn, pairs, d0, d1):
    try:
        import psycopg as pg
        connect = pg.connect
    except ImportError:
        try:
            import psycopg2 as pg
            connect = pg.connect
        except ImportError:
            sys.exit('No PostgreSQL driver. Run: pip install "psycopg[binary]"   or use --flights-csv (see --help).')
    rows = []
    with connect(dsn) as con:
        cur = con.cursor()
        for p in pairs:
            a, b = p.split("-")
            cur.execute(FLIGHTS_SQL, {"a": a, "b": b, "d0": d0, "d1": d1})
            cols = [c[0] for c in cur.description]
            got = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in got:
                r["pair"] = p
            log(f"{p}: {len(got)} flights in NavTrace")
            rows += got
    return rows


def flights_from_csv(path, pairs):
    want = {frozenset(p.split("-")): p for p in pairs}
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            p = want.get(frozenset((r["adep"], r["ades"])))
            if p and r.get("callsign"):
                r["pair"] = p
                rows.append(r)
    for p in pairs:
        log(f"{p}: {sum(1 for r in rows if r['pair'] == p)} flights in the CSV")
    return rows


# ------------------------------------------------------------------ step 2: API
class Api:
    def __init__(self, base, out, timeout, retries, rate):
        self.base, self.timeout, self.retries = base.rstrip("/"), timeout, retries
        self.cache = out / "raw"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.min_gap = 1.0 / rate if rate else 0
        self.lock, self.last = threading.Lock(), 0.0

    def path(self, cs, dof):
        return self.cache / f"{cs}_{dof}.json"

    def get(self, cs, dof):
        p = self.path(cs, dof)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")), None
            except ValueError:
                p.unlink()
        url = f"{self.base}/api/flights?" + urllib.parse.urlencode({"callsign": cs, "dof": dof})
        err = None
        for k in range(self.retries + 1):
            with self.lock:  # polite: at most `rate` requests per second in total
                wait = self.last + self.min_gap - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self.last = time.monotonic()
            try:
                with urllib.request.urlopen(url, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                p.write_text(json.dumps(data), encoding="utf-8")
                return data, None
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    data = {"flights": []}
                    p.write_text(json.dumps(data), encoding="utf-8")
                    return data, None
                err = f"HTTP {e.code}"
            except Exception as e:  # noqa: BLE001
                err = f"{e.__class__.__name__}: {e}"
            time.sleep(1.5 * (k + 1))
        return None, err


# ------------------------------------------------------------------ step 3: choose the plan
def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def choose(payload, adep, ades, first_seen):
    """The plan for this flight: aerodromes must match; nearest EOBT to the first ADS-B time wins."""
    t0 = parse_ts(first_seen + "Z") if first_seen else None
    best = None
    for fl in (payload or {}).get("flights") or []:
        f = fl.get("flight") or {}
        if f.get("adep") != adep or f.get("ades") != ades:
            continue
        e = parse_ts(f.get("eobt"))
        gap = abs((e - t0).total_seconds()) / 3600 if (e and t0) else 99.0
        if best is None or gap < best[0]:
            best = (gap, fl)
    return best


def cptea_records(fl, callsign, dof):
    """One record per message (FPL, CHG, CNL...) in the archive format CPTEA imports."""
    f = fl.get("flight") or {}
    out = []
    for m in fl.get("messages") or []:
        raw = (m.get("content") or "").replace("\r", "")
        if raw.strip():
            out.append({"id": f.get("id", ""), "callsign": callsign, "dof": dof, "adep": f.get("adep", ""),
                        "ades": f.get("ades", ""), "route": f.get("route", ""), "typeOfAircraft": f.get("typeOfAircraft", ""),
                        "level": f.get("level", ""), "eobt": f.get("eobt", ""), "atd": f.get("atd", ""),
                        "ata": f.get("ata", ""), "status": f.get("status", ""), "createdAt": m.get("createdAt", ""),
                        "fpl": raw})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--db", help='libpq connection string, e.g. "host=172.20.25.30 dbname=navtrace_flight user=..."')
    src.add_argument("--flights-csv", help="CSV with gufi,callsign,dof,adep,ades,first_seen_utc (see the psql command below)")
    ap.add_argument("--api", default="http://172.20.17.117:3000", help="flight data API base URL")
    ap.add_argument("--pairs", default=",".join(DEFAULT_PAIRS), help="comma-separated city pairs (either direction)")
    ap.add_argument("--from", dest="d0", help="first date of flight (YYYY-MM-DD)")
    ap.add_argument("--to", dest="d1", help="last date of flight (YYYY-MM-DD)")
    ap.add_argument("--out", default="fpl_city_pairs", help="output folder")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rate", type=float, default=8, help="maximum API requests per second (all workers together)")
    ap.add_argument("--timeout", type=float, default=15)
    ap.add_argument("--retries", type=int, default=2)
    a = ap.parse_args()
    pairs = [p.strip().upper().replace("–", "-") for p in a.pairs.split(",") if p.strip()]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    flights = flights_from_db(a.db, pairs, a.d0, a.d1) if a.db else flights_from_csv(a.flights_csv, pairs)
    if not flights:
        sys.exit("No flights found for these city pairs.")
    api = Api(a.api, out, a.timeout, a.retries, a.rate)
    try:  # fail fast when the API is out of reach (not on the AirNav network or VPN)
        urllib.request.urlopen(f"{api.base}/api/flights?callsign=TEST0&dof=2000-01-01", timeout=8)
    except urllib.error.HTTPError:
        pass
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Flight data API not reachable at {api.base}: {e}. Run this on the AirNav network or VPN.")

    # one request per callsign and date; flights just after midnight UTC may be filed under the previous date
    keys = sorted({(f["callsign"].strip().upper(), str(f["dof"])[:10]) for f in flights})
    log(f"{len(flights)} flights, {len(keys)} callsign/date combinations to ask the API for")
    answers, errors = {}, {}
    t_start, done = time.time(), 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(api.get, cs, d): (cs, d) for cs, d in keys}
        for fu in as_completed(futs):
            k = futs[fu]
            data, err = fu.result()
            if err:
                errors[k] = err
            else:
                answers[k] = data
            done += 1
            if done % 200 == 0 or done == len(keys):
                rate = done / max(1e-6, time.time() - t_start)
                log(f"  {done}/{len(keys)} answered ({rate:.1f}/s, about {(len(keys) - done) / max(rate, 1e-6) / 60:.0f} min left)")

    # match each flight to its plan; retry the previous date for early-UTC departures
    prev_keys = set()
    for f in flights:
        cs, d = f["callsign"].strip().upper(), str(f["dof"])[:10]
        if not choose(answers.get((cs, d)), f["adep"], f["ades"], f.get("first_seen_utc")):
            fs = f.get("first_seen_utc") or ""
            if fs[11:13] and int(fs[11:13]) < 3:
                prev_keys.add((cs, (date.fromisoformat(d) - timedelta(days=1)).isoformat()))
    prev_keys -= set(answers)
    if prev_keys:
        log(f"{len(prev_keys)} early-morning flights: also trying the previous date of flight")
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            for k, (data, err) in zip(prev_keys, ex.map(lambda k: api.get(*k), prev_keys)):
                if not err:
                    answers[k] = data

    per_pair = defaultdict(list)
    stats = defaultdict(lambda: defaultdict(int))
    missing = []
    for f in flights:
        cs, d = f["callsign"].strip().upper(), str(f["dof"])[:10]
        direction = f"{f['adep']}-{f['ades']}"
        st = stats[(f["pair"], direction)]
        st["flights"] += 1
        if (cs, d) in errors:
            st["api_error"] += 1
            missing.append({**f, "reason": "API error: " + errors[(cs, d)]})
            continue
        hit = choose(answers.get((cs, d)), f["adep"], f["ades"], f.get("first_seen_utc"))
        if not hit:
            prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
            hit = choose(answers.get((cs, prev)), f["adep"], f["ades"], f.get("first_seen_utc"))
        if not hit:
            got = (answers.get((cs, d)) or {}).get("flights") or []
            reason = "not in the flight plan system" if not got else \
                "plans returned, but none with these aerodromes: " + ", ".join(
                    sorted({f"{(x.get('flight') or {}).get('adep')}-{(x.get('flight') or {}).get('ades')}" for x in got}))
            st["no_plan"] += 1
            missing.append({**f, "reason": reason})
            continue
        gap, fl = hit
        recs = cptea_records(fl, cs, d)
        st["with_plan"] += 1
        st["with_route"] += bool((fl.get("flight") or {}).get("route"))
        st["eobt_over_3h"] += gap != 99.0 and gap > 3
        st["messages"] += len(recs)
        if not recs:
            st["no_raw_message"] += 1
        per_pair[f["pair"]] += recs

    for p in pairs:
        fn = out / f"fpl_{p.replace('-', '_')}.json"
        fn.write_text(json.dumps(per_pair.get(p, []), ensure_ascii=False, indent=0), encoding="utf-8")
    with (out / "coverage_summary.csv").open("w", newline="", encoding="utf-8") as fh:
        cols = ["pair", "direction", "flights", "with_plan", "pct_with_plan", "with_route", "messages", "no_plan",
                "api_error", "no_raw_message", "eobt_over_3h"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for (p, d), st in sorted(stats.items()):
            w.writerow({"pair": p, "direction": d, **{c: st.get(c, 0) for c in cols[2:] if c != "pct_with_plan"},
                        "pct_with_plan": f"{100 * st['with_plan'] / max(1, st['flights']):.1f}"})
    with (out / "flights_without_plan.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["pair", "gufi", "callsign", "dof", "adep", "ades", "first_seen_utc", "reason"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(missing)

    log("\nCity pair   Direction    Flights  With plan")
    for (p, d), st in sorted(stats.items()):
        log(f"{p:<11} {d:<11} {st['flights']:>8}  {st['with_plan']:>6} ({100 * st['with_plan'] / max(1, st['flights']):.0f}%)")
    log(f"\nWritten to {out.resolve()}: fpl_<PAIR>.json (import in CPTEA), coverage_summary.csv, flights_without_plan.csv")


if __name__ == "__main__":
    main()
