"""Database entities (SRS section 10.1)."""
from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


ROLES = ("admin", "analyst", "reviewer", "viewer")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default="analyst")
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    adep: Mapped[str] = mapped_column(String(4))
    ades: Mapped[str] = mapped_column(String(4))
    adep_lat: Mapped[float] = mapped_column(Float)
    adep_lon: Mapped[float] = mapped_column(Float)
    ades_lat: Mapped[float] = mapped_column(Float)
    ades_lon: Mapped[float] = mapped_column(Float)
    period_start: Mapped[str] = mapped_column(String(10), default="")
    period_end: Mapped[str] = mapped_column(String(10), default="")
    # {"THA431": "VTBS-WADD", ...}
    callsigns: Mapped[dict] = mapped_column(JSON, default=dict)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    members = relationship("ProjectMember", cascade="all, delete-orphan", back_populates="project")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(16), default="viewer")  # reviewer | viewer | analyst
    project = relationship("Project", back_populates="members")


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(128), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="loading")  # loading|ready|error
    message: Mapped[str] = mapped_column(Text, default="")
    n_files: Mapped[int] = mapped_column(Integer, default=0)
    n_flights: Mapped[int] = mapped_column(Integer, default=0)
    load_log: Mapped[list] = mapped_column(JSON, default=list)
    quality: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Flight(Base):
    __tablename__ = "flights"
    __table_args__ = (UniqueConstraint("dataset_id", "fid"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    fid: Mapped[str] = mapped_column(String(64), index=True)
    callsign: Mapped[str] = mapped_column(String(16), index=True)
    dof: Mapped[str] = mapped_column(String(10), index=True)
    adep: Mapped[str] = mapped_column(String(4), default="")
    ades: Mapped[str] = mapped_column(String(4), default="")
    n_points: Mapped[int] = mapped_column(Integer, default=0)
    t_first: Mapped[int] = mapped_column(Integer, default=0)
    t_last: Mapped[int] = mapped_column(Integer, default=0)
    duration_min: Mapped[float] = mapped_column(Float, default=0)
    lat0: Mapped[float] = mapped_column(Float, default=0)
    lon0: Mapped[float] = mapped_column(Float, default=0)
    lat1: Mapped[float] = mapped_column(Float, default=0)
    lon1: Mapped[float] = mapped_column(Float, default=0)
    max_alt: Mapped[float] = mapped_column(Float, default=0)
    gate_to_gate: Mapped[bool] = mapped_column(Boolean, default=False)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    exclude_reason: Mapped[str] = mapped_column(String(255), default="")


class FlightPlanRecord(Base):
    __tablename__ = "flight_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    callsign: Mapped[str] = mapped_column(String(16), index=True)
    dof: Mapped[str] = mapped_column(String(10), index=True)
    source: Mapped[str] = mapped_column(String(8), default="file")  # file | api
    source_ref: Mapped[str] = mapped_column(String(128), default="")
    filed_time: Mapped[str] = mapped_column(String(32), default="")
    msg_type: Mapped[str] = mapped_column(String(8), default="FPL")
    raw: Mapped[str] = mapped_column(Text, default="")
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    atd: Mapped[str] = mapped_column(String(32), default="")
    ata: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Fix(Base):
    __tablename__ = "fixes"
    __table_args__ = (UniqueConstraint("name", "source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(16), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(16), default="waypoint")
    source: Mapped[str] = mapped_column(String(32), default="import")  # navearth | import | manual
    airac: Mapped[str] = mapped_column(String(64), default="")


class Airway(Base):
    __tablename__ = "airways"
    __table_args__ = (UniqueConstraint("name", "source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(16), index=True)
    fixes: Mapped[list] = mapped_column(JSON, default=list)  # ordered list of fix names
    source: Mapped[str] = mapped_column(String(32), default="import")


class Airspace(Base):
    __tablename__ = "airspaces"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    ansp: Mapped[str] = mapped_column(String(64), default="")
    geometry: Mapped[dict] = mapped_column(JSON, default=dict)  # GeoJSON geometry


class ParameterSet(Base):
    __tablename__ = "parameter_sets"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, default=1)
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    readonly: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    paramset_id: Mapped[int] = mapped_column(ForeignKey("parameter_sets.id"))
    name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|error
    progress: Mapped[float] = mapped_column(Float, default=0)
    step: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    software_version: Mapped[str] = mapped_column(String(16), default="")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Insight(Base):
    __tablename__ = "insights"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    rule: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255), default="")
    drafted: Mapped[str] = mapped_column(Text, default="")
    edited: Mapped[str] = mapped_column(Text, default="")
    numbers: Mapped[dict] = mapped_column(JSON, default=dict)
    chart: Mapped[str] = mapped_column(String(64), default="")
    effect: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(16), default="drafted")  # drafted|accepted|edited|rejected
    kind: Mapped[str] = mapped_column(String(16), default="insight")  # insight | recommendation
    editor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|in_review|released
    files: Mapped[dict] = mapped_column(JSON, default=dict)  # {"pdf": path, "docx": path, "pptx": path}
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    released_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64))
    obj_type: Mapped[str] = mapped_column(String(32), default="")
    obj_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
