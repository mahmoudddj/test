"""Project-level operations. Projects are created by the XNAT administrator, never by this platform."""

from __future__ import annotations

from irtcri.integrations.xnat.connection import XNATConnection, XNATError


def project_path(project: str) -> str:
    return f"/data/projects/{project}"


def get_project(conn: XNATConnection, project: str) -> dict:
    try:
        return conn.get_json(project_path(project))
    except XNATError as e:
        if e.status in (403, 404):
            raise XNATError(e.status, "GET", project_path(project),
                            "project missing or not accessible - ask the XNAT admin to create it and grant "
                            "the service account member/owner access") from e
        raise
