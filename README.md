# CPTEA — City-Pair Trajectory Efficiency Analyser

CPTEA is a web application for the TBO WG3 end-to-end city-pair efficiency analysis. It implements the ENRI End-to-End Analysis Methodology v0.3 with CAAS comments and ENRI's leg-based cruise method. Every open parameter defaults to the value used in AirNav Indonesia's VTBS–WADD study.

It covers:

- **Login and roles.** Four roles: Administrator, Analyst, Reviewer and Viewer. Sessions time out after 30 minutes idle, accounts lock after repeated failed sign-ins, and every action is written to an audit log.
- **Data loading.**
  - ADS-B tracks: FR24 CSV files, a ZIP, a folder or a server path.
  - Flight plans: AirNav FPL archive files (JSON or Parquet), or the AirNav flight data API, which needs the VPN.
  - Aeronautical data: NavEarth WFS refresh (points, navaids, ATS routes, TMA polygons, with an address administrators can edit), fixes and airways from CSV, FIR boundaries from GeoJSON.
- **All metrics of the method.**
  - Path extension against the filed route at several en-route boundaries.
  - En-route time and its variance, lateral deviation, and efficiency by FIR.
  - Route classification.
  - Terminal arrival: extra time, holding-type turns and predictability.
  - The ENRI leg-based cruise vertical score, capped climbs, and planned against flown level changes.
  - Climb and descent level-offs, with a sensitivity analysis on the duration threshold.
- **Settings.** An interface for every parameter, with versioned parameter sets, JSON import and export, and a map editor for the classification cut lines.
- **Visualisation.**
  - Accumulated results with filters.
  - Track density maps.
  - A per-flight view: map, vertical profile, speed and vertical rate, lateral deviation, the flight's metrics against its group, and its flight-plan messages.
  - Run comparison.
- **Insights and reports.**
  - Rule-based insights that analysts accept, edit or reject.
  - PDF, Word and PowerPoint reports with a draft → in review → released workflow.

The user manual is in [`docs/CPTEA_User_Manual.pdf`](docs/CPTEA_User_Manual.pdf), with the source in [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md).

## Stack

| Layer | Technology |
|---|---|
| API and analysis | Python 3.12+, FastAPI, SQLAlchemy 2 (SQLite by default, PostgreSQL optional), pandas/NumPy, PyArrow (Parquet per dataset and run), scikit-learn (k-means), matplotlib |
| Reports | reportlab (PDF), python-docx (Word), python-pptx (PowerPoint) |
| Web interface | React 18, Vite, ECharts, Leaflet (with a built-in offline coastline) |
| Packaging | One Docker image. The API serves the built interface. |

## Quick start with Docker

```bash
cp .env.example .env              # set CPTEA_SECRET_KEY at least
docker compose up -d --build
```

Open http://localhost:8000 and sign in as `admin` with the password from `.env` (`ChangeMe!2026` by default). You must choose a new password at first sign-in.

Data lives in the `cptea-data` volume. To load a large track archive that is already on the server, mount it read-only (see the commented line in `docker-compose.yml`). Then, in the app, enter `/archive` as the folder path on the server.

## Without Docker

Requirements: Python 3.12 or newer, and Node 20 or newer.

```bash
./scripts/start.sh                # creates a venv, builds the interface, serves on :8000
```

For development, use `./scripts/dev.sh`. It runs the API with auto-reload on port 8000 and Vite on port 5173.

Run a single worker (`--workers 1`). Runs, plan fetches and report generation are background threads inside the API process.

## Configuration

All settings are environment variables; see [`.env.example`](.env.example). The main ones:

| Variable | Default | Purpose |
|---|---|---|
| `CPTEA_SECRET_KEY` | (development value) | Signs login tokens. **Set it in production.** |
| `CPTEA_DATA_DIR` | `./data` | Database, uploads, dataset and run files |
| `CPTEA_DB_URL` | SQLite in the data folder | SQLAlchemy URL, for example PostgreSQL |
| `CPTEA_ADMIN_USERNAME` / `CPTEA_ADMIN_PASSWORD` | `admin` / `ChangeMe!2026` | First administrator, created on first start only |
| `CPTEA_FLIGHT_API_URL` / `CPTEA_FLIGHT_API_TOKEN` | `http://172.20.17.117:3000` / empty | AirNav flight data API (VPN only) |
| `CPTEA_NAVEARTH_WFS` | NavEarth `enroute_chart` WFS (TMA polygon GetFeature link) | Default NavEarth address. Administrators can change it, and the layer names, on the Aeronautical data page. A saved value overrides this variable. |
| `CPTEA_TILE_URL` | OpenStreetMap | Map tiles. Leave it empty on air-gapped servers: maps then use the built-in coastline. |

## Tests

```bash
cd backend
pip install -r requirements.txt
python -m pytest -q tests                                        # unit tests
CPTEA_TEST_DATA=/path/to/vtbs_wadd python -m pytest -q tests     # plus end-to-end acceptance
```

The acceptance test loads the VTBS–WADD data: `Flights/` holds the FR24 CSV tree and `FPL/` holds `FPL_VTBS_WADD.json` and `fplaug25.parquet.gzip`. It runs the full analysis with the default parameters and checks the reference results:

| Result | Reference value |
|---|---|
| Path extension at the 100 NM rings | −10.6 NM VTBS→WADD, −1.4 NM WADD→VTBS |
| En-route time | 187.3 / 173.9 min |
| Holding-type arrivals | WADD 13.3 %, VTBS 5.1 % |
| WADD descent level-offs | 45.2 % at the 20 s threshold, 8.8 % at 300 s |

It then accepts the insights, generates the three report formats, and checks the per-flight view and the exports.

## Repository layout

```
backend/app/
  engine/      geo, fpl, tracks, analysis, pipeline, aggregate, insights, ingest, detail
  routers/     auth, projects (datasets, parameters), data (plans, AIS), runs, reports
  reports/     charts + PDF/Word/PowerPoint builder
  seed/        VTBS–WADD fixes and airways
  defaults.py  every parameter with its VTBS–WADD default, range and source
frontend/src/  pages (project, admin), components (Chart, MapView, ParamEditor, FilterBar)
docs/          user manual (Markdown + PDF), screenshots, manual build tools
```

To rebuild the manual PDF after editing it:

```bash
python3 docs/tools/gen_appendix.py && python3 docs/tools/build_manual.py
```

The build needs Playwright with Chromium and `pdftotext`.
