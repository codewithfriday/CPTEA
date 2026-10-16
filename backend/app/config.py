"""Application configuration, read from environment variables (see .env.example)."""
import os
from pathlib import Path

APP_NAME = "CPTEA"
APP_TITLE = "City-Pair Trajectory Efficiency Analyser"
VERSION = "1.0.0"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CPTEA_DATA_DIR", BASE_DIR.parent.parent / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_URL = os.environ.get("CPTEA_DB_URL", f"sqlite:///{DATA_DIR / 'cptea.db'}")
SECRET_KEY = os.environ.get("CPTEA_SECRET_KEY", "change-me-in-production-please")
JWT_ALGORITHM = "HS256"

# FR-AUTH-03 / FR-AUTH-04
SESSION_IDLE_MINUTES = int(os.environ.get("CPTEA_SESSION_IDLE_MINUTES", "30"))
MAX_FAILED_LOGINS = int(os.environ.get("CPTEA_MAX_FAILED_LOGINS", "5"))
LOCKOUT_MINUTES = int(os.environ.get("CPTEA_LOCKOUT_MINUTES", "15"))
MIN_PASSWORD_LENGTH = 10

# Initial administrator (created on first start only)
ADMIN_USERNAME = os.environ.get("CPTEA_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("CPTEA_ADMIN_PASSWORD", "ChangeMe!2026")

# External sources
NAVEARTH_WFS = os.environ.get(
    "CPTEA_NAVEARTH_WFS",
    "https://app-pia.airnavindonesia.co.id/geoserver/enroute_chart/ows?service=WFS&version=1.0.0&request=GetFeature"
    "&typeName=enroute_chart:tma_polygon&outputFormat=application/json",
)
FLIGHT_API_URL = os.environ.get("CPTEA_FLIGHT_API_URL", "http://172.20.17.117:3000")
FLIGHT_API_TOKEN = os.environ.get("CPTEA_FLIGHT_API_TOKEN", "")

TILE_URL = os.environ.get("CPTEA_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")

STATIC_DIR = Path(os.environ.get("CPTEA_STATIC_DIR", BASE_DIR.parent.parent / "frontend" / "dist"))


def dataset_dir(dataset_id: int) -> Path:
    p = DATA_DIR / "datasets" / str(dataset_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_dir(run_id: int) -> Path:
    p = DATA_DIR / "runs" / str(run_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def upload_dir() -> Path:
    p = DATA_DIR / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p
