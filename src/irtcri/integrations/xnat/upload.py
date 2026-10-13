"""File upload with write-once semantics for original images."""

from __future__ import annotations

from irtcri.core.provenance import sha256_bytes
from irtcri.integrations.xnat.connection import XNATConnection
from irtcri.integrations.xnat.download import download_file


class XNATImmutableError(RuntimeError):
    pass


def upload_file(conn: XNATConnection, parent_path: str, resource: str, name: str, data: bytes,
                content_type: str = "application/octet-stream", overwrite: bool = False) -> str:
    """PUT a file. Without ``overwrite`` an existing file is never replaced: identical content is accepted
    (idempotent re-upload), different content raises ``XNATImmutableError``. Returns the REST path."""
    path = f"{parent_path}/resources/{resource}/files/{name}"
    params = {"inbody": "true"}
    if overwrite:
        params["overwrite"] = "true"
    r = conn.request("PUT", path, params=params, content=data, headers={"Content-Type": content_type}, ok=(409,))
    if r.status_code == 409:
        existing = download_file(conn, path)
        if sha256_bytes(existing) != sha256_bytes(data):
            raise XNATImmutableError(f"refusing to overwrite different content at {path}")
    return path
