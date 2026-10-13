"""FastAPI dependencies: DB session, authentication, authorisation."""

from __future__ import annotations

import uuid
from typing import Callable, Iterator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.security import Perm, Principal, Role, hash_token
from irtcri.core.timeutils import utcnow
from irtcri.db.models import User
from irtcri.db.session import session_factory

bearer = HTTPBearer(auto_error=False)


def get_db() -> Iterator[Session]:
    s = session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_principal(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                  db: Session = Depends(get_db)) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    u = db.scalar(select(User).where(User.token_hash == hash_token(creds.credentials)))
    if u is None or not u.active or (u.token_expires and u.token_expires < utcnow()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    return Principal(user_id=u.id, username=u.username, role=Role(u.role), site_id=u.site_id)


def require(perm: Perm) -> Callable[..., Principal]:
    def dep(who: Principal = Depends(get_principal)) -> Principal:
        if not who.can(perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"role {who.role} lacks permission '{perm}'")
        return who
    return dep
