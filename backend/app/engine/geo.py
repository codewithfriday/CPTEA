"""Geodesy helpers. Distances in NM (SRS FR-ANA-04)."""
import numpy as np

R_NM = 3440.065
WGS84_A_NM = 6378137.0 / 1852.0
WGS84_F = 1 / 298.257223563


def gc(la1, lo1, la2, lo2):
    """Great-circle (haversine) distance in NM on a sphere; vectorised."""
    la1, lo1, la2, lo2 = map(np.radians, (la1, lo1, la2, lo2))
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * R_NM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def ellipsoid(la1, lo1, la2, lo2):
    """Andoyer-Lambert approximation of the WGS84 geodesic (error well under 0.01% at these ranges)."""
    d_sph = gc(la1, lo1, la2, lo2) / R_NM  # central angle
    la1, lo1, la2, lo2 = map(np.radians, (la1, lo1, la2, lo2))
    with np.errstate(invalid="ignore", divide="ignore"):
        P = (la1 + la2) / 2
        Q = (la2 - la1) / 2
        s = d_sph
        X = (s - np.sin(s)) * (np.sin(P) ** 2 * np.cos(Q) ** 2) / np.cos(s / 2) ** 2
        Y = (s + np.sin(s)) * (np.cos(P) ** 2 * np.sin(Q) ** 2) / np.sin(s / 2) ** 2
        d = WGS84_A_NM * (s - WGS84_F / 2 * (X + Y))
    return np.where(s > 0, d, 0.0)


def dist_fn(model: str):
    return ellipsoid if model == "wgs84" else gc


def bearing(la1, lo1, la2, lo2):
    la1, lo1, la2, lo2 = map(np.radians, (la1, lo1, la2, lo2))
    y = np.sin(lo2 - lo1) * np.cos(la2)
    x = np.cos(la1) * np.sin(la2) - np.sin(la1) * np.cos(la2) * np.cos(lo2 - lo1)
    return np.degrees(np.arctan2(y, x))


def path_length(la, lo, dist=gc):
    if len(la) < 2:
        return 0.0
    return float(np.sum(dist(la[:-1], lo[:-1], la[1:], lo[1:])))


def cumulative(la, lo, dist=gc):
    if len(la) < 2:
        return np.zeros(len(la))
    return np.r_[0, np.cumsum(dist(la[:-1], lo[:-1], la[1:], lo[1:]))]


def densify(points, step_nm=1.0):
    """points: list of (lat, lon). Returns lat, lon arrays densified roughly every step_nm along straight legs."""
    la, lo = [], []
    for (a, b), (c, d) in zip(points[:-1], points[1:]):
        k = max(2, int(gc(a, b, c, d) / step_nm))
        la.extend(np.linspace(a, c, k, endpoint=False))
        lo.extend(np.linspace(b, d, k, endpoint=False))
    if points:
        la.append(points[-1][0])
        lo.append(points[-1][1])
    return np.array(la), np.array(lo)


def ring_window(la, lo, dep_ll, ades_ll, R):
    """Index of first point >= R NM from ADEP and last point >= R NM from ADES. Returns (i, j) or None."""
    da = gc(la, lo, *dep_ll)
    dd = gc(la, lo, *ades_ll)
    i = np.where(da >= R)[0]
    j = np.where(dd >= R)[0]
    if len(i) == 0 or len(j) == 0 or j[-1] <= i[0]:
        return None
    return int(i[0]), int(j[-1])


def crossing(lat, lon, value, orientation="latitude"):
    """Coordinate where a polyline first crosses a latitude (returns lon) or meridian (returns lat)."""
    y, x = (lat, lon) if orientation == "latitude" else (lon, lat)
    s = np.sign(y - value)
    idx = np.where(np.diff(s) != 0)[0]
    if len(idx) == 0:
        return np.nan
    i = idx[0]
    if y[i + 1] == y[i]:
        return float(x[i])
    f = (value - y[i]) / (y[i + 1] - y[i])
    return float(x[i] + f * (x[i + 1] - x[i]))
