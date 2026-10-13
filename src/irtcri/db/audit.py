"""Audit-trail writer. Every create/update/void/export goes through here."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from irtcri.core.security import Principal
from irtcri.db.models import AuditEvent


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def row_snapshot(obj: Any) -> dict[str, Any]:
    return {c.key: _jsonable(getattr(obj, c.key)) for c in inspect(obj).mapper.column_attrs}


def audit(session: Session, who: Principal, action: str, entity_type: str, entity_id: str | None,
          before: dict | None = None, after: dict | None = None, reason: str | None = None,
          request_id: str | None = None, client_ip: str | None = None) -> AuditEvent:
    if before and after:   # store only changed fields for updates
        changed = {k for k in after if before.get(k) != after.get(k)}
        before = {k: before.get(k) for k in changed}
        after = {k: after[k] for k in changed}
    ev = AuditEvent(user_id=who.user_id, username=who.username, role=str(who.role), action=action,
                    entity_type=entity_type, entity_id=entity_id, before=before, after=after, reason=reason,
                    request_id=request_id, client_ip=client_ip)
    session.add(ev)
    return ev
