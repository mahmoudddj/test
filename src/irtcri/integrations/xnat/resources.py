"""Resources (folders of files) at scan or session level."""

from __future__ import annotations

from irtcri.integrations.xnat.connection import XNATConnection


def ensure_resource(conn: XNATConnection, parent_path: str, label: str, fmt: str = "", content: str = "") -> None:
    conn.request("PUT", f"{parent_path}/resources/{label}", params={"format": fmt, "content": content},
                 ok=(409,))   # 409: resource already exists


def list_files(conn: XNATConnection, parent_path: str, label: str) -> list[dict]:
    """Rows with ``Name``, ``Size``, ``URI`` and (XNAT >= 1.7) ``digest`` (MD5)."""
    from irtcri.integrations.xnat.connection import XNATError
    try:
        return conn.result_set(f"{parent_path}/resources/{label}/files")
    except XNATError as e:
        if e.status == 404:
            return []
        raise


def list_resources(conn: XNATConnection, parent_path: str) -> list[str]:
    return [r.get("label") for r in conn.result_set(f"{parent_path}/resources")]
