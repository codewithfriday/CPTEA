"""Parameter definitions and the VTBS-WADD default set (SRS section 6.2).

Each entry: id, tab, label, type, default, unit, min, max, options, source, help.
Types: number, int, list (of numbers), choice, bool, text.
"""

TABS = ["General", "Flight plans", "Classification", "Horizontal", "Terminal", "Vertical", "Cruise", "Reporting"]

P = []


def p(id, tab, label, type, default, unit="", min=None, max=None, options=None, source="VTBS–WADD", help="",
      admin_only=False, steps=()):
    P.append(dict(id=id, tab=tab, label=label, type=type, default=default, unit=unit, min=min, max=max,
                  options=options or [], source=source, help=help, admin_only=admin_only, steps=list(steps)))


# General
p("GEN-01", "General", "Earth model", "choice", "spherical", options=["spherical", "wgs84"],
  source="ENRI v0.3, VTBS–WADD", help="Spherical uses R = 3,440.065 NM; WGS84 uses the ellipsoid (slower).",
  steps=["all"])
p("GEN-02", "General", "Airport radius for gate-to-gate check", "number", 5, "NM", 1, 20,
  help="First and last track points must lie within this distance of the expected aerodromes.", steps=["load"])
p("GEN-03", "General", "Minimum valid flight duration", "number", 150, "min", 0, 600,
  help="Tracks shorter than this are excluded (about 60% of the pair's median block time).", steps=["load"])
p("GEN-04", "General", "Data gap threshold", "number", 300, "s", 30, 3600, help="Airborne gap flagged.",
  steps=["quality"])
p("GEN-05", "General", "Major gap threshold", "number", 600, "s", 60, 7200, help="Airborne gap flagged as major.",
  steps=["quality"])
p("GEN-06", "General", "Resample interval", "number", 10, "s", 1, 60,
  help="Uniform resampling interval for vertical logic.", steps=["vertical", "cruise"])
p("GEN-07", "General", "Largest gap interpolated for vertical logic", "number", 90, "s", 10, 600,
  help="Longer gaps are marked missing and never produce level segments.", steps=["vertical", "cruise"])

# Flight plans
p("FPL-01", "Flight plans", "Flight plan version used", "choice", "last_before_eobt",
  options=["last_before_eobt", "first_filed", "last_received"], help="Which FPL version is the filed plan.",
  steps=["fpl"])
p("FPL-02", "Flight plans", "Flights with unresolved fixes", "choice", "exclude",
  options=["exclude", "straight_line"], help="Exclude from reference-based metrics, or bridge the gap with a straight line.",
  steps=["horizontal", "cruise"])
p("FPL-03", "Flight plans", "Flight plan source", "choice", "file", options=["file", "api", "api_with_file_fallback"],
  source="VTBS–WADD (file)", help="Where flight plans come from for this project.", steps=["fpl"])
p("FPL-04", "Flight plans", "Flight data API base URL", "text", "http://172.20.17.117:3000", source="AirNav",
  help="AirNav flight data API (VPN only). Administrators only.", admin_only=True, steps=["fpl"])
p("FPL-05", "Flight plans", "API timeout and retries", "list", [10, 3], "s, retries", source="proposed",
  help="Request timeout in seconds and number of retries.", steps=["fpl"])
p("FPL-06", "Flight plans", "API concurrent requests", "int", 4, "", 1, 16, source="proposed", steps=["fpl"])
p("FPL-07", "Flight plans", "Cache refresh age", "int", 30, "days", 1, 365, source="proposed",
  help="Cached API responses older than this are fetched again.", steps=["fpl"])
p("FPL-08", "Flight plans", "Match window when several flights returned", "number", 3, "h", 0.5, 12,
  source="proposed", help="EOBT must lie within this window of track take-off.", steps=["fpl"])

# Classification
p("CLU-01", "Classification", "Classification clip radius", "number", 100, "NM", 0, 500,
  help="Track ends within this distance of each airport are clipped before clustering.", steps=["classify"])
p("CLU-02", "Classification", "Cut lines", "list", [11, 8, 5, 2, -1, -4, -6.5], "° latitude",
  help="Latitudes (north–south flows) or meridians (east–west flows) crossing the corridor.", steps=["classify"])
p("CLU-02b", "Classification", "Cut line orientation", "choice", "latitude", options=["latitude", "longitude"],
  help="Latitude lines for north–south flows, meridians for east–west.", steps=["classify"])
p("CLU-03", "Classification", "Number of clusters k (per direction)", "int", 1, "", 1, 10, steps=["classify"])
p("CLU-04", "Classification", "Minimum cluster share kept in metrics", "number", 5, "% of flights", 0, 50,
  source="proposed", steps=["classify"])
p("CLU-05", "Classification", "Outlier distance from centroid", "number", 3, "× MAD", 1, 10, source="proposed",
  help="A track is an outlier when any crossing lies further from its centroid than this many robust spreads "
       "and at least the minimum distance (CLU-05b).", steps=["classify"])
p("CLU-05b", "Classification", "Minimum outlier distance", "number", 1.5, "°", 0.1, 10, source="proposed",
  help="Tracks are never outliers for deviations below this (tactical directs stay in).", steps=["classify"])

# Horizontal
p("HOR-01", "Horizontal", "En-route boundary ring", "number", 100, "NM", 0, 300,
  help="Ring around ADEP and ADES that delimits the en-route portion.", steps=["horizontal"])
p("HOR-01b", "Horizontal", "Boundary sensitivity rings", "list", [40, 100, 200], "NM",
  help="Metrics 1–4 are recomputed for each ring for comparison.", steps=["horizontal"])
p("HOR-02", "Horizontal", "Variance percentiles", "list", [15, 85], "percentile", source="ENRI v0.3",
  help="Flight-time variance = (P_high − P_low) / 2.", steps=["aggregate"])
p("HOR-03", "Horizontal", "Sign of path extension", "choice", "signed", options=["signed", "absolute"],
  steps=["horizontal"])
p("HOR-04", "Horizontal", "Lateral deviation bin", "number", 1, "° latitude", 0.5, 5, steps=["horizontal"])

# Terminal
p("TER-01", "Terminal", "Terminal rings", "list", [40, 100], "NM", steps=["terminal"])
p("TER-02", "Terminal", "Unimpeded reference percentile", "number", 20, "percentile", 5, 50,
  help="Unimpeded time inside the 100 NM ring = this percentile of all flights.", steps=["terminal"])
p("TER-03", "Terminal", "Holding detection", "list", [300, 60], "°, NM",
  help="Net heading change threshold, and distance from ADES within which it is measured.", steps=["terminal"])

# Vertical
p("VER-01", "Vertical", "Vertical-rate window", "number", 60, "s", 10, 180, source="CAAS, VTBS–WADD",
  steps=["vertical"])
p("VER-02", "Vertical", "Level threshold |VR|", "number", 5, "ft/s", 1, 20, source="CAAS",
  help="5 ft/s = 300 ft/min.", steps=["vertical"])
p("VER-03", "Vertical", "Minimum level-off duration", "number", 20, "s", 10, 900, source="CAAS",
  steps=["vertical"])
p("VER-03b", "Vertical", "Level-off sensitivity durations", "list", [20, 40, 60, 90, 120, 180, 240, 300], "s",
  steps=["vertical"])
p("VER-04", "Vertical", "Cruise level minimum duration", "number", 10, "min", 2, 30, steps=["vertical"])
p("VER-05", "Vertical", "Level change step", "number", 900, "ft", 300, 2000, steps=["vertical"])
p("VER-06", "Vertical", "TOC / TOD rule", "choice", "filed_level", options=["filed_level", "track_only"],
  help="filed_level: TOC = first time the flight reaches the filed initial level; fallback track-only "
       "(first / last level ≥ cruise minimum).", steps=["vertical"])
p("VER-07", "Vertical", "Climb level-off bounds", "choice", "enri", options=["enri", "caas", "custom"],
  source="ENRI v0.3", help="ENRI: 11,000 ft to TOC. CAAS: below 33,000 ft. Custom: VER-07c.", steps=["vertical"])
p("VER-07c", "Vertical", "Custom climb bounds", "list", [11000, 45000], "ft", steps=["vertical"])
p("VER-08", "Vertical", "Descent level-off bounds", "choice", "enri", options=["enri", "caas", "custom"],
  source="ENRI v0.3", help="ENRI: TOD to 11,000 ft. CAAS: 33,000 to 7,500 ft. Custom: VER-08c.", steps=["vertical"])
p("VER-08c", "Vertical", "Custom descent bounds", "list", [11000, 45000], "ft", steps=["vertical"])
p("VER-09", "Vertical", "Reporting altitude bands", "list", [1500, 3500, 11000, 17500, 50000], "ft (edges)",
  steps=["aggregate"])
p("VER-10", "Vertical", "Final-approach intercept exclusion", "list", [3500, 20], "ft, NM",
  help="Level segments below this altitude within this distance of ADES are excluded (0 = off).", steps=["vertical"])
p("VER-11", "Vertical", "Capped-climb margin", "number", 500, "ft", 200, 2000,
  help="First level segment ≥ 5 min more than this below the filed initial level = capped climb.", steps=["vertical"])

# Cruise
p("CRU-01", "Cruise", "Waypoint check scope", "number", 200, "NM", 0, 500,
  help="Only waypoints further than this from both airports are checked.", steps=["cruise"])
p("CRU-02", "Cruise", "Track window around a waypoint", "number", 5, "min", 0, 15, steps=["cruise"])
p("CRU-03", "Cruise", "Maximum CPA distance", "number", 15, "NM", 1, 50, steps=["cruise"])
p("CRU-04", "Cruise", "Lateral tolerance for leg assignment", "number", 60, "NM", 10, 150, source="proposed",
  steps=["cruise"])
p("CRU-05", "Cruise", "'At filed level' tolerance", "number", 500, "ft", 100, 1000, steps=["cruise"])
p("CRU-06", "Cruise", "Deviation class edges", "list", [500, 1500], "ft",
  help="|Δh| ≤ first edge = at filed; ≤ second = 1,000 ft; above = ≥ 2,000 ft.", steps=["cruise"])
p("CRU-07", "Cruise", "Penalty function", "choice", "unit", options=["unit", "below_only", "openap_fuel"],
  source="proposed", help="unit: 1 per 1,000 ft per minute, above and below reported separately; "
                          "below_only: only flight below filed is penalised; openap_fuel: OpenAP fuel flow (if installed).",
  steps=["cruise"])
p("CRU-08", "Cruise", "Planned-change match distance", "number", 50, "NM", 10, 300, steps=["cruise"])

# Reporting
p("REP-01", "Reporting", "Report language", "choice", "en", options=["en"], source="proposed", steps=["report"])
p("REP-02", "Reporting", "Insight thresholds", "dict", {
    "INS-01": 1.2, "INS-02": 10, "INS-03": 0.2, "INS-04": -1, "INS-06": 0.3, "INS-07": 20, "INS-08": 25,
    "INS-09": 10, "INS-10": 50, "INS-11": 30, "INS-12": 1.3, "INS-13": 25, "INS-14": 50},
  source="proposed", help="Thresholds for each insight rule (SRS 8.2).", steps=["insights"])

PARAMS = {d["id"]: d for d in P}
DEFAULT_VALUES = {d["id"]: d["default"] for d in P}
DEFAULT_SET_NAME = "VTBS–WADD default"


def validate(values: dict, is_admin: bool, base: dict | None = None) -> dict:
    """FR-SET-03: validate types and ranges; return a complete value dict."""
    out = dict(DEFAULT_VALUES)
    if base:
        out.update({k: v for k, v in base.items() if k in PARAMS})
    errors = []
    for k, v in (values or {}).items():
        d = PARAMS.get(k)
        if not d:
            continue
        if d["admin_only"] and not is_admin and base is not None and v != base.get(k):
            errors.append(f"{k}: only administrators can change this field")
            continue
        try:
            if d["type"] in ("number", "int"):
                v = float(v)
                if d["type"] == "int":
                    v = int(round(v))
                if d["min"] is not None and v < d["min"] or d["max"] is not None and v > d["max"]:
                    errors.append(f"{k}: {v} outside {d['min']}–{d['max']}")
                    continue
            elif d["type"] == "list":
                if isinstance(v, str):
                    v = [float(x) for x in v.replace(";", ",").split(",") if x.strip()]
                v = [float(x) for x in v]
                if not v:
                    errors.append(f"{k}: at least one value required")
                    continue
            elif d["type"] == "choice":
                if v not in d["options"]:
                    errors.append(f"{k}: must be one of {d['options']}")
                    continue
            elif d["type"] == "dict":
                if not isinstance(v, dict):
                    errors.append(f"{k}: must be an object")
                    continue
                v = {**d["default"], **{kk: float(vv) for kk, vv in v.items()}}
            elif d["type"] == "bool":
                v = bool(v)
            else:
                v = str(v)
        except (TypeError, ValueError):
            errors.append(f"{k}: invalid value")
            continue
        out[k] = v
    if errors:
        from fastapi import HTTPException
        raise HTTPException(422, "; ".join(errors))
    return out


def diff_from_default(values: dict) -> dict:
    return {k: v for k, v in values.items() if k in DEFAULT_VALUES and v != DEFAULT_VALUES[k]}
