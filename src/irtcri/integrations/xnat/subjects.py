"""Subjects = pseudonymous study participants (label = Study ID, never a name/MRN)."""

from __future__ import annotations

from irtcri.integrations.xnat.connection import XNATConnection
from irtcri.integrations.xnat.projects import project_path


def subject_path(project: str, subject: str) -> str:
    return f"{project_path(project)}/subjects/{subject}"


def ensure_subject(conn: XNATConnection, project: str, label: str, fields: dict[str, str] | None = None) -> None:
    params = {f"xnat:subjectData/fields/field[name={k}]/field": v for k, v in (fields or {}).items()}
    conn.request("PUT", subject_path(project, label), params=params)


def list_subjects(conn: XNATConnection, project: str) -> list[str]:
    return [r["label"] for r in conn.result_set(f"{project_path(project)}/subjects")]
