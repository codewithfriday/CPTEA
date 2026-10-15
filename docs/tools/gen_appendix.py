"""Generate the manual's parameter and insight appendices from the code, so they never drift."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.defaults import PARAMS, TABS  # noqa: E402

INSIGHTS = {
    "INS-01": "Arrival time variance is larger than en-route time variance by the threshold factor",
    "INS-02": "The share of holding-type arrivals exceeds the threshold (%)",
    "INS-03": "En-route time and arrival time are barely correlated (|r| below the threshold): delay is absorbed in the terminal area",
    "INS-04": "Median path extension against the filed route is below the threshold (NM): flights fly shorter than filed",
    "INS-05": "Path extension changes sign between boundary radii",
    "INS-06": "A direction uses a single tight route cluster (spread below the threshold, degrees)",
    "INS-07": "A direction/type flies above the filed level at more than the threshold share of cruise waypoints (%)",
    "INS-08": "Fewer than the threshold share of flown level changes happen near the filed waypoint (%)",
    "INS-09": "More than the threshold share of climbs are capped below the filed level (%)",
    "INS-10": "The level-off share falls by more than the threshold (%) between the shortest and longest duration threshold",
    "INS-11": "One altitude band holds more than the threshold share of level time (%)",
    "INS-12": "A month's arrival extra time exceeds the period mean by the threshold factor",
    "INS-13": "Aircraft types differ by more than the threshold in share of cruise above the filed level (percentage points)",
    "INS-14": "Some callsign-months have fewer than the threshold share of the usual number of flights (%)",
}


def val(v):
    if isinstance(v, (list, dict)):
        s = json.dumps(v, ensure_ascii=False)
        return s if len(s) < 60 else s[:57] + "…"
    return str(v)


def esc(s):
    return str(s).replace("|", "\\|")


out = ["# Appendix A. Parameter reference", "",
       "Defaults are the VTBS–WADD values shipped with CPTEA. *Source* says where each value comes from. "
       "Parameters marked † can only be changed by an Administrator.", ""]
for tab in TABS:
    out += [f"## {tab}", "", "| ID | Parameter | Default | Unit | Range / options | Source |", "|---|---|---|---|---|---|"]
    for d in PARAMS.values():
        if d["tab"] != tab:
            continue
        rng = ", ".join(map(str, d["options"])) if d["options"] else (
            f"{d['min']}–{d['max']}" if d["min"] is not None or d["max"] is not None else "")
        name = d["label"] + (" †" if d.get("admin_only") else "")
        out.append(f"| {d['id']} | {esc(name)}<br><span class='help'>{esc(d.get('help', ''))}</span> | {esc(val(d['default']))} | {d['unit']} | {esc(rng)} | {esc(d['source'])} |")
    out.append("")
th = PARAMS["REP-02"]["default"]
out += ["# Appendix B. Insight rules", "",
        "Each rule drafts an insight when its condition is met. Thresholds are parameter REP-02 and can be changed per project.", "",
        "| Rule | Fires when | Default threshold |", "|---|---|---|"]
for k, v in INSIGHTS.items():
    out.append(f"| {k} | {v} | {th.get(k, '–')} |")
out.append("")
(ROOT / "docs" / "APPENDIX.md").write_text("\n".join(out), encoding="utf-8")
print("written", len(out), "lines")
