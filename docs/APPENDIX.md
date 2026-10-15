# Appendix A. Parameter reference

Defaults are the VTBS–WADD values shipped with CPTEA. *Source* says where each value comes from. Parameters marked † can only be changed by an Administrator.

## General

| ID | Parameter | Default | Unit | Range / options | Source |
|---|---|---|---|---|---|
| GEN-01 | Earth model<br><span class='help'>Spherical uses R = 3,440.065 NM; WGS84 uses the ellipsoid (slower).</span> | spherical |  | spherical, wgs84 | ENRI v0.3, VTBS–WADD |
| GEN-02 | Airport radius for gate-to-gate check<br><span class='help'>First and last track points must lie within this distance of the expected aerodromes.</span> | 5 | NM | 1–20 | VTBS–WADD |
| GEN-03 | Minimum valid flight duration<br><span class='help'>Tracks shorter than this are excluded (about 60% of the pair's median block time).</span> | 150 | min | 0–600 | VTBS–WADD |
| GEN-04 | Data gap threshold<br><span class='help'>Airborne gap flagged.</span> | 300 | s | 30–3600 | VTBS–WADD |
| GEN-05 | Major gap threshold<br><span class='help'>Airborne gap flagged as major.</span> | 600 | s | 60–7200 | VTBS–WADD |
| GEN-06 | Resample interval<br><span class='help'>Uniform resampling interval for vertical logic.</span> | 10 | s | 1–60 | VTBS–WADD |
| GEN-07 | Largest gap interpolated for vertical logic<br><span class='help'>Longer gaps are marked missing and never produce level segments.</span> | 90 | s | 10–600 | VTBS–WADD |

## Flight plans

| ID | Parameter | Default | Unit | Range / options | Source |
|---|---|---|---|---|---|
| FPL-01 | Flight plan version used<br><span class='help'>Which FPL version is the filed plan.</span> | last_before_eobt |  | last_before_eobt, first_filed, last_received | VTBS–WADD |
| FPL-02 | Flights with unresolved fixes<br><span class='help'>Exclude from reference-based metrics, or bridge the gap with a straight line.</span> | exclude |  | exclude, straight_line | VTBS–WADD |
| FPL-03 | Flight plan source<br><span class='help'>Where flight plans come from for this project.</span> | file |  | file, api, api_with_file_fallback | VTBS–WADD (file) |
| FPL-04 | Flight data API base URL †<br><span class='help'>AirNav flight data API (VPN only). Administrators only.</span> | http://172.20.17.117:3000 |  |  | AirNav |
| FPL-05 | API timeout and retries<br><span class='help'>Request timeout in seconds and number of retries.</span> | [10, 3] | s, retries |  | proposed |
| FPL-06 | API concurrent requests<br><span class='help'></span> | 4 |  | 1–16 | proposed |
| FPL-07 | Cache refresh age<br><span class='help'>Cached API responses older than this are fetched again.</span> | 30 | days | 1–365 | proposed |
| FPL-08 | Match window when several flights returned<br><span class='help'>EOBT must lie within this window of track take-off.</span> | 3 | h | 0.5–12 | proposed |

## Classification

| ID | Parameter | Default | Unit | Range / options | Source |
|---|---|---|---|---|---|
| CLU-01 | Classification clip radius<br><span class='help'>Track ends within this distance of each airport are clipped before clustering.</span> | 100 | NM | 0–500 | VTBS–WADD |
| CLU-02 | Cut lines<br><span class='help'>Latitudes (north–south flows) or meridians (east–west flows) crossing the corridor.</span> | [11, 8, 5, 2, -1, -4, -6.5] | ° latitude |  | VTBS–WADD |
| CLU-02b | Cut line orientation<br><span class='help'>Latitude lines for north–south flows, meridians for east–west.</span> | latitude |  | latitude, longitude | VTBS–WADD |
| CLU-03 | Number of clusters k (per direction)<br><span class='help'></span> | 1 |  | 1–10 | VTBS–WADD |
| CLU-04 | Minimum cluster share kept in metrics<br><span class='help'></span> | 5 | % of flights | 0–50 | proposed |
| CLU-05 | Outlier distance from centroid<br><span class='help'>A track is an outlier when any crossing lies further from its centroid than this many robust spreads and at least the minimum distance (CLU-05b).</span> | 3 | × MAD | 1–10 | proposed |
| CLU-05b | Minimum outlier distance<br><span class='help'>Tracks are never outliers for deviations below this (tactical directs stay in).</span> | 1.5 | ° | 0.1–10 | proposed |

## Horizontal

| ID | Parameter | Default | Unit | Range / options | Source |
|---|---|---|---|---|---|
| HOR-01 | En-route boundary ring<br><span class='help'>Ring around ADEP and ADES that delimits the en-route portion.</span> | 100 | NM | 0–300 | VTBS–WADD |
| HOR-01b | Boundary sensitivity rings<br><span class='help'>Metrics 1–4 are recomputed for each ring for comparison.</span> | [40, 100, 200] | NM |  | VTBS–WADD |
| HOR-02 | Variance percentiles<br><span class='help'>Flight-time variance = (P_high − P_low) / 2.</span> | [15, 85] | percentile |  | ENRI v0.3 |
| HOR-03 | Sign of path extension<br><span class='help'></span> | signed |  | signed, absolute | VTBS–WADD |
| HOR-04 | Lateral deviation bin<br><span class='help'></span> | 1 | ° latitude | 0.5–5 | VTBS–WADD |

## Terminal

| ID | Parameter | Default | Unit | Range / options | Source |
|---|---|---|---|---|---|
| TER-01 | Terminal rings<br><span class='help'></span> | [40, 100] | NM |  | VTBS–WADD |
| TER-02 | Unimpeded reference percentile<br><span class='help'>Unimpeded time inside the 100 NM ring = this percentile of all flights.</span> | 20 | percentile | 5–50 | VTBS–WADD |
| TER-03 | Holding detection<br><span class='help'>Net heading change threshold, and distance from ADES within which it is measured.</span> | [300, 60] | °, NM |  | VTBS–WADD |

## Vertical

| ID | Parameter | Default | Unit | Range / options | Source |
|---|---|---|---|---|---|
| VER-01 | Vertical-rate window<br><span class='help'></span> | 60 | s | 10–180 | CAAS, VTBS–WADD |
| VER-02 | Level threshold \|VR\|<br><span class='help'>5 ft/s = 300 ft/min.</span> | 5 | ft/s | 1–20 | CAAS |
| VER-03 | Minimum level-off duration<br><span class='help'></span> | 20 | s | 10–900 | CAAS |
| VER-03b | Level-off sensitivity durations<br><span class='help'></span> | [20, 40, 60, 90, 120, 180, 240, 300] | s |  | VTBS–WADD |
| VER-04 | Cruise level minimum duration<br><span class='help'></span> | 10 | min | 2–30 | VTBS–WADD |
| VER-05 | Level change step<br><span class='help'></span> | 900 | ft | 300–2000 | VTBS–WADD |
| VER-06 | TOC / TOD rule<br><span class='help'>filed_level: TOC = first time the flight reaches the filed initial level; fallback track-only (first / last level ≥ cruise minimum).</span> | filed_level |  | filed_level, track_only | VTBS–WADD |
| VER-07 | Climb level-off bounds<br><span class='help'>ENRI: 11,000 ft to TOC. CAAS: below 33,000 ft. Custom: VER-07c.</span> | enri |  | enri, caas, custom | ENRI v0.3 |
| VER-07c | Custom climb bounds<br><span class='help'></span> | [11000, 45000] | ft |  | VTBS–WADD |
| VER-08 | Descent level-off bounds<br><span class='help'>ENRI: TOD to 11,000 ft. CAAS: 33,000 to 7,500 ft. Custom: VER-08c.</span> | enri |  | enri, caas, custom | ENRI v0.3 |
| VER-08c | Custom descent bounds<br><span class='help'></span> | [11000, 45000] | ft |  | VTBS–WADD |
| VER-09 | Reporting altitude bands<br><span class='help'></span> | [1500, 3500, 11000, 17500, 50000] | ft (edges) |  | VTBS–WADD |
| VER-10 | Final-approach intercept exclusion<br><span class='help'>Level segments below this altitude within this distance of ADES are excluded (0 = off).</span> | [3500, 20] | ft, NM |  | VTBS–WADD |
| VER-11 | Capped-climb margin<br><span class='help'>First level segment ≥ 5 min more than this below the filed initial level = capped climb.</span> | 500 | ft | 200–2000 | VTBS–WADD |

## Cruise

| ID | Parameter | Default | Unit | Range / options | Source |
|---|---|---|---|---|---|
| CRU-01 | Waypoint check scope<br><span class='help'>Only waypoints further than this from both airports are checked.</span> | 200 | NM | 0–500 | VTBS–WADD |
| CRU-02 | Track window around a waypoint<br><span class='help'></span> | 5 | min | 0–15 | VTBS–WADD |
| CRU-03 | Maximum CPA distance<br><span class='help'></span> | 15 | NM | 1–50 | VTBS–WADD |
| CRU-04 | Lateral tolerance for leg assignment<br><span class='help'></span> | 60 | NM | 10–150 | proposed |
| CRU-05 | 'At filed level' tolerance<br><span class='help'></span> | 500 | ft | 100–1000 | VTBS–WADD |
| CRU-06 | Deviation class edges<br><span class='help'>\|Δh\| ≤ first edge = at filed; ≤ second = 1,000 ft; above = ≥ 2,000 ft.</span> | [500, 1500] | ft |  | VTBS–WADD |
| CRU-07 | Penalty function<br><span class='help'>unit: 1 per 1,000 ft per minute, above and below reported separately; below_only: only flight below filed is penalised; openap_fuel: OpenAP fuel flow (if installed).</span> | unit |  | unit, below_only, openap_fuel | proposed |
| CRU-08 | Planned-change match distance<br><span class='help'></span> | 50 | NM | 10–300 | VTBS–WADD |

## Reporting

| ID | Parameter | Default | Unit | Range / options | Source |
|---|---|---|---|---|---|
| REP-01 | Report language<br><span class='help'></span> | en |  | en | proposed |
| REP-02 | Insight thresholds<br><span class='help'>Thresholds for each insight rule (SRS 8.2).</span> | {"INS-01": 1.2, "INS-02": 10, "INS-03": 0.2, "INS-04": -1… |  |  | proposed |

# Appendix B. Insight rules

Each rule drafts an insight when its condition is met. Thresholds are parameter REP-02 and can be changed per project.

| Rule | Fires when | Default threshold |
|---|---|---|
| INS-01 | Arrival time variance is larger than en-route time variance by the threshold factor | 1.2 |
| INS-02 | The share of holding-type arrivals exceeds the threshold (%) | 10 |
| INS-03 | En-route time and arrival time are barely correlated (|r| below the threshold): delay is absorbed in the terminal area | 0.2 |
| INS-04 | Median path extension against the filed route is below the threshold (NM): flights fly shorter than filed | -1 |
| INS-05 | Path extension changes sign between boundary radii | – |
| INS-06 | A direction uses a single tight route cluster (spread below the threshold, degrees) | 0.3 |
| INS-07 | A direction/type flies above the filed level at more than the threshold share of cruise waypoints (%) | 20 |
| INS-08 | Fewer than the threshold share of flown level changes happen near the filed waypoint (%) | 25 |
| INS-09 | More than the threshold share of climbs are capped below the filed level (%) | 10 |
| INS-10 | The level-off share falls by more than the threshold (%) between the shortest and longest duration threshold | 50 |
| INS-11 | One altitude band holds more than the threshold share of level time (%) | 30 |
| INS-12 | A month's arrival extra time exceeds the period mean by the threshold factor | 1.3 |
| INS-13 | Aircraft types differ by more than the threshold in share of cruise above the filed level (percentage points) | 25 |
| INS-14 | Some callsign-months have fewer than the threshold share of the usual number of flights (%) | 50 |
