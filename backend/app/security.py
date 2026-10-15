"""Authentication, roles and audit (FR-AUTH, FR-USR)."""
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import AuditLog, Project, ProjectMember, User, now

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

ROLE_RANK = {"viewer": 0, "reviewer": 1, "analyst": 2, "admin": 3}


def hash_password(p: str) -> str:
    return pwd.hash(p)


def verify_password(p: str, h: str) -> bool:
    try:
        return pwd.verify(p, h)
    except Exception:
        return False


def check_password_policy(p: str):
    if len(p or "") < config.MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must have at least {config.MIN_PASSWORD_LENGTH} characters")


def make_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=config.SESSION_IDLE_MINUTES)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp}, config.SECRET_KEY,
                      algorithm=config.JWT_ALGORITHM)


def audit(db: Session, user: User | None, action: str, obj_type: str = "", obj_id="", detail: dict | None = None):
    db.add(AuditLog(user_id=user.id if user else None, username=user.username if user else "",
                    action=action, obj_type=obj_type, obj_id=str(obj_id), detail=detail or {}))
    db.commit()


def current_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> User:
    token = creds.credentials if creds else request.query_params.get("token")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please log in again")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account inactive")
    return user


def require_role(min_role: str):
    def dep(user: User = Depends(current_user)) -> User:
        if ROLE_RANK.get(user.role, -1) < ROLE_RANK[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {min_role} role")
        return user
    return dep


def project_access(db: Session, user: User, project_id: int, need: str = "viewer") -> Project:
    """FR-USR-02: role checks on every call. Admins and owners have full access; members get their level."""
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    if user.role == "admin" or p.owner_id == user.id:
        return p
    m = db.query(ProjectMember).filter_by(project_id=project_id, user_id=user.id).first()
    level = m.level if m else None
    if user.role == "analyst" and level is None and need == "viewer":
        # analysts can browse all projects read-only
        level = "viewer"
    if level is None:
        raise HTTPException(403, "No access to this project")
    eff = min(ROLE_RANK.get(level, 0), ROLE_RANK.get(user.role, 0))
    if eff < ROLE_RANK[need]:
        raise HTTPException(403, f"Requires {need} access to this project")
    return p


def login(db: Session, username: str, password: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    t = now()
    if user and user.locked_until and user.locked_until > t:
        audit(db, user, "login_blocked", "user", user.id)
        raise HTTPException(423, f"Account locked until {user.locked_until:%H:%M} UTC")
    if not user or not user.active or not verify_password(password, user.password_hash):
        if user:
            user.failed_attempts += 1
            if user.failed_attempts >= config.MAX_FAILED_LOGINS:
                user.locked_until = t + timedelta(minutes=config.LOCKOUT_MINUTES)
                user.failed_attempts = 0
            db.commit()
        audit(db, user, "login_failed", "user", user.id if user else "", {"username": username})
        raise HTTPException(401, "Invalid username or password")
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login = t
    db.commit()
    audit(db, user, "login", "user", user.id)
    return user
