import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..models import ROLES, AuditLog, User
from ..security import (audit, check_password_policy, current_user, hash_password, login, make_token,
                        require_role, verify_password)

router = APIRouter(prefix="/api", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


def user_out(u: User):
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role, "active": u.active,
            "must_change_password": u.must_change_password, "last_login": u.last_login,
            "locked_until": u.locked_until, "created_at": u.created_at}


@router.post("/auth/login")
def do_login(body: LoginIn, db: Session = Depends(get_db)):
    u = login(db, body.username.strip(), body.password)
    return {"token": make_token(u), "user": user_out(u), "idle_minutes": config.SESSION_IDLE_MINUTES}


@router.post("/auth/refresh")
def refresh(user: User = Depends(current_user)):
    return {"token": make_token(user)}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


class PwIn(BaseModel):
    old_password: str
    new_password: str


@router.post("/auth/change-password")
def change_password(body: PwIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "Current password is wrong")
    check_password_policy(body.new_password)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    db.commit()
    audit(db, user, "password_changed", "user", user.id)
    return {"ok": True}


class UserIn(BaseModel):
    username: str | None = None
    full_name: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = None


@router.get("/users")
def list_users(user: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    return [user_out(u) for u in db.query(User).order_by(User.username).all()]


@router.get("/users/directory")
def directory(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Minimal list for sharing projects."""
    return [{"id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role}
            for u in db.query(User).filter(User.active).order_by(User.username).all()]


@router.post("/users")
def create_user(body: UserIn, admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    if not body.username or not body.password:
        raise HTTPException(400, "Username and password are required")
    if db.query(User).filter_by(username=body.username).first():
        raise HTTPException(409, "Username already exists")
    if body.role and body.role not in ROLES:
        raise HTTPException(400, "Unknown role")
    check_password_policy(body.password)
    u = User(username=body.username.strip(), full_name=body.full_name or "", role=body.role or "analyst",
             password_hash=hash_password(body.password), must_change_password=True)
    db.add(u)
    db.commit()
    audit(db, admin, "user_created", "user", u.id, {"username": u.username, "role": u.role})
    return user_out(u)


@router.patch("/users/{uid}")
def update_user(uid: int, body: UserIn, admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "User not found")
    before = user_out(u)
    if body.full_name is not None:
        u.full_name = body.full_name
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, "Unknown role")
        u.role = body.role
    if body.active is not None:
        if u.id == admin.id and not body.active:
            raise HTTPException(400, "You cannot deactivate yourself")
        u.active = body.active
    if body.password:
        check_password_policy(body.password)
        u.password_hash = hash_password(body.password)
        u.must_change_password = True
        u.locked_until = None
        u.failed_attempts = 0
    db.commit()
    after = user_out(u)
    audit(db, admin, "user_updated", "user", u.id,
          {"before": {k: str(v) for k, v in before.items()}, "after": {k: str(v) for k, v in after.items()},
           "password_reset": bool(body.password)})
    return after


@router.get("/audit")
def audit_list(limit: int = 500, action: str = "", username: str = "", admin: User = Depends(require_role("admin")),
               db: Session = Depends(get_db)):
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action.contains(action))
    if username:
        q = q.filter(AuditLog.username == username)
    rows = q.order_by(AuditLog.id.desc()).limit(min(limit, 5000)).all()
    return [{"id": r.id, "ts": r.ts, "username": r.username, "action": r.action, "obj_type": r.obj_type,
             "obj_id": r.obj_id, "detail": r.detail} for r in rows]


@router.get("/audit.csv")
def audit_csv(admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "time_utc", "username", "action", "object_type", "object_id", "detail"])
    for r in db.query(AuditLog).order_by(AuditLog.id).all():
        w.writerow([r.id, r.ts, r.username, r.action, r.obj_type, r.obj_id, r.detail])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=cptea_audit.csv"})
