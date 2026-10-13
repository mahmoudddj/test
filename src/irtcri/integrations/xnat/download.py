"""File download with checksum verification."""

from __future__ import annotations

from irtcri.core.provenance import sha256_bytes
from irtcri.integrations.xnat.connection import XNATConnection


class XNATChecksumError(RuntimeError):
    pass


def download_file(conn: XNATConnection, path: str) -> bytes:
    return conn.request("GET", path).content


def download_verified(conn: XNATConnection, path: str, sha256: str | None) -> bytes:
    data = download_file(conn, path)
    if sha256 is not None and sha256_bytes(data) != sha256:
        raise XNATChecksumError(f"checksum mismatch for {path}")
    return data
