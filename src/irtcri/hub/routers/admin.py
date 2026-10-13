from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from irtcri.core.config import get_settings
from irtcri.core.security import Perm, Principal, Role
from irtcri.db.models import AuditEvent, Site, User
from irtcri.hub import schemas as S
from irtcri.hub import services as svc
from irtcri.hub.deps import get_db, get_principal, require

router = APIRouter(tags=["admin"])


@router.post("/auth/bootstrap", summary="Create the first Admin (only when no users exist)")
def bootstrap(username: str, x_bootstrap_token: str = Header(...), db: Session = Depends(get_db)):
    expected = get_settings().bootstrap_admin_token
    if expected is None or not secrets.compare_digest(x_bootstrap_token, expected.get_secret_value()):
        raise HTTPException(403, "invalid bootstrap token")
    if db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(409, "already bootstrapped")
    who = Principal(None, "bootstrap", Role.ADMIN)
    u, token = svc.create_user(db, who, S.UserIn(username=username, role="Admin"))
    return {"username": u.username, "role": u.role, "token": token,
            "note": "Store this token securely; it is shown only once."}


@router.get("/auth/me")
def me(who: Principal = Depends(get_principal)):
    return {"user_id": who.user_id, "username": who.username, "role": who.role, "site_id": who.site_id,
            "permissions": sorted(p.value for p in Perm if who.can(p))}


@router.post("/users", status_code=201)
def create_user(body: S.UserIn, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.MANAGE_USERS))):
    u, token = svc.create_user(db, who, body)
    return {"id": u.id, "username": u.username, "role": u.role, "token": token}


@router.post("/users/{username}/deactivate")
def deactivate(username: str, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.MANAGE_USERS))):
    u = db.scalar(select(User).where(User.username == username))
    if u is None:
        raise HTTPException(404, "unknown user")
    u.active = False
    from irtcri.db.audit import audit
    audit(db, who, "deactivate", "app_user", u.id)
    return {"username": username, "active": False}


@router.get("/sites")
def list_sites(db: Session = Depends(get_db), who: Principal = Depends(get_principal)):
    sites = db.scalars(select(Site)).all()
    return [{"id": s.id, "site_code": s.site_code, "name": s.name, "country": s.country, "timezone": s.timezone,
             "setting": s.setting, "active": s.active} for s in sites if who.can_access_site(s.id)]


@router.post("/sites", status_code=201)
def create_site(body: S.SiteIn, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.MANAGE_SITES))):
    s = svc.create_site(db, who, body)
    return {"id": s.id, "site_code": s.site_code}


@router.get("/audit")
def audit_log(entity_id: str | None = None, entity_type: str | None = None, limit: int = Query(200, le=5000),
              db: Session = Depends(get_db), who: Principal = Depends(require(Perm.READ_AUDIT))):
    q = select(AuditEvent).order_by(AuditEvent.event_time.desc()).limit(limit)
    if entity_id:
        q = q.where(AuditEvent.entity_id == entity_id)
    if entity_type:
        q = q.where(AuditEvent.entity_type == entity_type)
    return [svc.as_dict(e) for e in db.scalars(q)]


