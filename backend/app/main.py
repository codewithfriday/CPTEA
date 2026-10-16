"""CPTEA - City-Pair Trajectory Efficiency Analyser. FastAPI application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import Base, SessionLocal, engine
from .defaults import DEFAULT_SET_NAME, DEFAULT_VALUES
from .engine import ingest
from .models import ParameterSet, Run, User
from .routers import auth, data, projects, reports, runs
from .security import hash_password


def migrate():
    """Add columns introduced after a database was created (create_all only adds missing tables)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("airspaces")} if insp.has_table("airspaces") else None
    if cols is not None:
        with engine.begin() as con:
            if "kind" not in cols:
                con.execute(text("ALTER TABLE airspaces ADD COLUMN kind VARCHAR(16) DEFAULT 'fir'"))
            if "props" not in cols:
                con.execute(text("ALTER TABLE airspaces ADD COLUMN props JSON"))
            if "source" not in cols:
                con.execute(text("ALTER TABLE airspaces ADD COLUMN source VARCHAR(32) DEFAULT 'import'"))


def bootstrap():
    Base.metadata.create_all(engine)
    migrate()
    db = SessionLocal()
    try:
        if not db.query(User).count():
            db.add(User(username=config.ADMIN_USERNAME, full_name="Administrator", role="admin",
                        password_hash=hash_password(config.ADMIN_PASSWORD), must_change_password=True))
        if not db.query(ParameterSet).filter(ParameterSet.project_id.is_(None)).count():
            db.add(ParameterSet(project_id=None, name=DEFAULT_SET_NAME, version=1, values=dict(DEFAULT_VALUES), readonly=True,
                                note="Values used in AirNav's VTBS–WADD analysis (SRS 6.2)"))
        else:
            # add parameters introduced by newer versions to existing sets
            for ps in db.query(ParameterSet).all():
                missing = {k: v for k, v in DEFAULT_VALUES.items() if k not in (ps.values or {})}
                if missing:
                    ps.values = {**ps.values, **missing}
        # runs interrupted by a restart
        for r in db.query(Run).filter(Run.status.in_(["queued", "running"])).all():
            r.status, r.message = "error", "Interrupted by a server restart; start the run again"
        db.commit()
        ingest.seed_navdata(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    yield


app = FastAPI(title=config.APP_TITLE, version=config.VERSION, lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
for r in (auth.router, projects.router, data.router, runs.router, reports.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"ok": True, "app": config.APP_NAME, "version": config.VERSION}


@app.get("/api/config")
def client_config():
    return {"tile_url": config.TILE_URL, "idle_minutes": config.SESSION_IDLE_MINUTES, "version": config.VERSION,
            "flight_api_url": config.FLIGHT_API_URL}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"Server error: {exc.__class__.__name__}: {exc}"})


if config.STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=config.STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = config.STATIC_DIR / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(config.STATIC_DIR / "index.html")
