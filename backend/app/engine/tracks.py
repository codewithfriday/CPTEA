"""Surveillance track import, cleaning and data-quality summary (FR-DATA-01..06, FR-DATA-14, FR-ANA-01..03)."""
import hashlib
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .geo import gc

FID_RE = re.compile(r"([A-Z0-9]{2,8})-(\d{4}-\d{2}-\d{2})")
REQUIRED = ["Timestamp", "Callsign", "Position", "Altitude"]


def read_track(src, name: str = "") -> pd.DataFrame:
    """Read one FR24-style CSV. src: path or bytes. Returns columns t, lat, lon, alt, gs, trk, callsign."""
    df = pd.read_csv(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns {missing}")
    ll = df["Position"].astype(str).str.split(",", expand=True)
    out = pd.DataFrame({
        "t": pd.to_numeric(df["Timestamp"], errors="coerce"),
        "lat": pd.to_numeric(ll[0], errors="coerce"),
        "lon": pd.to_numeric(ll[1], errors="coerce"),
        "alt": pd.to_numeric(df["Altitude"], errors="coerce"),
        "gs": pd.to_numeric(df.get("Speed", np.nan), errors="coerce"),
        "trk": pd.to_numeric(df.get("Direction", np.nan), errors="coerce"),
    })
    out["callsign"] = str(df["Callsign"].dropna().iloc[0]).strip().upper() if df["Callsign"].notna().any() else ""
    out = out.dropna(subset=["t", "lat", "lon", "alt"]).sort_values("t").drop_duplicates("t")
    out["t"] = out["t"].astype("int64")
    return out.reset_index(drop=True)


def map_columns(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """FR-DATA-16: map a generic ADS-B export to the FR24 column names."""
    df = df.rename(columns={v: k for k, v in mapping.items() if v})
    if "Position" not in df.columns and {"lat", "lon"} <= set(df.columns):
        df["Position"] = df["lat"].astype(str) + "," + df["lon"].astype(str)
    return df


def clean(tr: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """FR-ANA-01: drop physically impossible points (implied ground speed > 700 kt, or an altitude jump of more
    than 3,000 ft in under 10 s). Vectorised, repeated until stable (max 5 passes)."""
    removed = 0
    for _ in range(5):
        if len(tr) < 3:
            break
        la, lo = tr.lat.values, tr.lon.values
        t, a = tr.t.values.astype(float), tr.alt.values.astype(float)
        dt = np.diff(t)
        v = gc(la[:-1], lo[:-1], la[1:], lo[1:]) / np.maximum(dt, 1e-9) * 3600
        bad_in = (dt <= 0) | (v > 700) | ((dt < 10) & (np.abs(np.diff(a)) > 3000))
        bad = np.r_[False, bad_in]
        if not bad.any():
            break
        removed += int(bad.sum())
        tr = tr[~bad].reset_index(drop=True)
    return tr, removed


def flight_id(name: str, tr: pd.DataFrame) -> tuple[str, str, str]:
    m = FID_RE.search(Path(name).stem.upper()) if name else None
    if m:
        return f"{m.group(1)}-{m.group(2)}", m.group(1), m.group(2)
    cs = tr.callsign.iloc[0] if len(tr) else "UNKNOWN"
    d = pd.to_datetime(int(tr.t.iloc[0]), unit="s").strftime("%Y-%m-%d") if len(tr) else "1970-01-01"
    return f"{cs}-{d}", cs, d


def summarise_flight(tr: pd.DataFrame, project, params: dict) -> dict:
    a = (project.adep_lat, project.adep_lon)
    b = (project.ades_lat, project.ades_lon)
    lat0, lon0, lat1, lon1 = tr.lat.iloc[0], tr.lon.iloc[0], tr.lat.iloc[-1], tr.lon.iloc[-1]
    d0a, d0b = gc(lat0, lon0, *a), gc(lat0, lon0, *b)
    d1a, d1b = gc(lat1, lon1, *a), gc(lat1, lon1, *b)
    adep, ades = (project.adep, project.ades) if d0a + d1b <= d0b + d1a else (project.ades, project.adep)
    r = float(params["GEN-02"])
    start_ok = min(d0a, d0b) <= r
    end_ok = min(d1a, d1b) <= r
    dur = (tr.t.iloc[-1] - tr.t.iloc[0]) / 60
    flags = []
    if not start_ok:
        flags.append("start_not_at_airport")
    if not end_ok:
        flags.append("end_not_at_airport")
    if dur < float(params["GEN-03"]):
        flags.append("short_duration")
    exp = project.callsigns.get(tr.callsign.iloc[0]) if project.callsigns else None
    if exp and exp != f"{adep}-{ades}":
        flags.append("direction_mismatch")
    return dict(adep=adep, ades=ades, n_points=len(tr), t_first=int(tr.t.iloc[0]), t_last=int(tr.t.iloc[-1]),
                duration_min=float(dur), lat0=float(lat0), lon0=float(lon0), lat1=float(lat1), lon1=float(lon1),
                max_alt=float(tr.alt.max()), gate_to_gate=bool(start_ok and end_ok), flags=flags)


def evaluate_inclusion(f, params: dict) -> tuple[bool, str]:
    r = float(params["GEN-02"])
    reasons = []
    # recompute with the run's parameters from stored end points
    if "manual_exclude" in (f.flags or []):
        return False, f.exclude_reason or "excluded manually"
    if f.duration_min < float(params["GEN-03"]):
        reasons.append("short duration")
    if not f.gate_to_gate:
        reasons.append("not gate-to-gate")
    return (not reasons), ", ".join(reasons)


def gaps(points: pd.DataFrame, thr: float) -> pd.DataFrame:
    """FR-DATA-06: airborne gaps longer than thr seconds."""
    p = points.sort_values(["fid", "t"])
    g = p.groupby("fid")
    dt = g["t"].diff()
    pa = g["alt"].shift()
    m = (dt > thr) & (p["alt"] > 0) & (pa > 0)
    out = p[m].copy()
    out["dt"] = dt[m]
    out["plat"] = g["lat"].shift()[m]
    out["plon"] = g["lon"].shift()[m]
    out["mlat"] = (out.lat + out.plat) / 2
    out["mlon"] = (out.lon + out.plon) / 2
    return out[["fid", "mlat", "mlon", "alt", "dt"]]


def quality_summary(points: pd.DataFrame, flights: pd.DataFrame, params: dict) -> dict:
    """FR-DATA-14 coverage matrix, gate-to-gate share, sampling by altitude band, gaps."""
    q = {}
    f = flights.copy()
    f["month"] = f["dof"].str[:7]
    cov = f.groupby(["callsign", "month"]).size().unstack(fill_value=0)
    q["coverage"] = {"callsigns": cov.index.tolist(), "months": cov.columns.tolist(), "counts": cov.values.tolist()}
    q["n_flights"] = int(len(f))
    q["gate_to_gate_share"] = float(f.gate_to_gate.mean()) if len(f) else 0
    q["directions"] = f.groupby(["callsign", "adep", "ades"]).size().reset_index(name="n").to_dict("records")
    p = points.sort_values(["fid", "t"])
    dt = p.groupby("fid")["t"].diff()
    air = p[p.alt > 0].assign(dt=dt[p.alt > 0]).dropna(subset=["dt"])
    bands = []
    for lo, hi in [(1, 11000), (11000, 25000), (25000, 60000)]:
        x = air[(air.alt >= lo) & (air.alt < hi)].dt
        bands.append({"band": f"{lo:,}–{hi:,} ft", "median_s": float(x.median()) if len(x) else None,
                      "p90_s": float(x.quantile(.9)) if len(x) else None})
    q["sampling"] = bands
    g1 = gaps(points, float(params["GEN-04"]))
    g2 = g1[g1.dt > float(params["GEN-05"])]
    q["gaps"] = {"threshold_s": params["GEN-04"], "n": int(len(g1)), "flights": int(g1.fid.nunique()),
                 "major_threshold_s": params["GEN-05"], "n_major": int(len(g2)), "flights_major": int(g2.fid.nunique()),
                 "share_flights": float(g1.fid.nunique() / max(1, len(f))),
                 "points": g1.sample(min(len(g1), 500), random_state=0)[["mlat", "mlon", "alt", "dt"]].round(3).to_dict("records")}
    return q


def content_hash(paths_or_bytes) -> str:
    h = hashlib.sha256()
    for x in paths_or_bytes:
        h.update(x if isinstance(x, (bytes, bytearray)) else Path(x).read_bytes())
    return h.hexdigest()


def resample(tr: pd.DataFrame, step: float, max_gap: float, vr_window: float):
    """Uniform resampling (FR-ANA-02) and centred vertical rate (FR-ANA-03). Returns dict of arrays."""
    t = tr.t.values.astype(float)
    t0 = t[0]
    tt = np.arange(t0, t[-1] + 1e-6, step)
    alt = np.interp(tt, t, tr.alt.values.astype(float))
    lat = np.interp(tt, t, tr.lat.values)
    lon = np.interp(tt, t, tr.lon.values)
    idx = np.clip(np.searchsorted(t, tt), 1, len(t) - 1)
    gap = (t[idx] - t[idx - 1]) > max_gap
    h = max(1, int(round(vr_window / 2 / step)))
    vr = np.full_like(alt, np.nan)
    if len(alt) > 2 * h:
        vr[h:-h] = (alt[2 * h:] - alt[:-2 * h]) / (2 * h * step)
    vr[gap] = np.nan
    return {"t": tt, "alt": alt, "lat": lat, "lon": lon, "vr": vr, "gap": gap}


def segments(mask: np.ndarray):
    m = np.r_[False, mask, False].astype(int)
    d = np.diff(m)
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))
