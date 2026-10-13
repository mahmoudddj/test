"""Imaging sessions (XNAT experiments) = one acquisition time point of one catheter episode."""

from __future__ import annotations

from datetime import datetime

from irtcri.integrations.xnat.connection import XNATConnection
from irtcri.integrations.xnat.projects import project_path
from irtcri.integrations.xnat.subjects import subject_path


def session_path(project: str, subject: str, session: str) -> str:
    return f"{subject_path(project, subject)}/experiments/{session}"


def ensure_session(conn: XNATConnection, project: str, subject: str, label: str, xsi_type: str,
                   acquisition_time_utc: datetime, fields: dict[str, str] | None = None) -> None:
    params = {"xsiType": xsi_type,
              f"{xsi_type}/date": acquisition_time_utc.strftime("%m/%d/%Y"),
              f"{xsi_type}/time": acquisition_time_utc.strftime("%H:%M:%S")}
    params.update({f"{xsi_type}/fields/field[name={k}]/field": v for k, v in (fields or {}).items()})
    conn.request("PUT", session_path(project, subject, label), params=params)


def list_sessions(conn: XNATConnection, project: str, subject: str | None = None) -> list[dict]:
    """Rows with at least ``label`` and ``subject_label``."""
    if subject:
        rows = conn.result_set(f"{subject_path(project, subject)}/experiments")
        for r in rows:
            r.setdefault("subject_label", subject)
        return rows
    return conn.result_set(f"{project_path(project)}/experiments", {"columns": "label,subject_label,xsiType,date"})


def ensure_scan(conn: XNATConnection, project: str, subject: str, session: str, scan_id: str, xsi_type: str,
                scan_type: str, description: str = "") -> None:
    conn.request("PUT", f"{session_path(project, subject, session)}/scans/{scan_id}",
                 params={"xsiType": xsi_type, f"{xsi_type}/type": scan_type,
                         f"{xsi_type}/series_description": description})


def list_scans(conn: XNATConnection, project: str, subject: str, session: str) -> list[dict]:
    return conn.result_set(f"{session_path(project, subject, session)}/scans")
