"""Object storage abstraction (local filesystem for dev, S3/MinIO for deployment).

Raw objects are WRITE-ONCE: ``put_immutable`` refuses to overwrite an existing
key with different content. In S3/MinIO deployments enable bucket versioning
and Object Lock (compliance mode) on the raw bucket as an additional control
(docs/security_governance.md).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from irtcri.core.config import Settings, get_settings
from irtcri.core.provenance import sha256_bytes


class ImmutableObjectError(RuntimeError):
    pass


class ObjectStore(Protocol):
    def put_immutable(self, bucket: str, key: str, data: bytes) -> str: ...
    def put(self, bucket: str, key: str, data: bytes) -> str: ...
    def get(self, bucket: str, key: str) -> bytes: ...
    def exists(self, bucket: str, key: str) -> bool: ...
    def list(self, bucket: str, prefix: str = "") -> list[str]: ...


class LocalObjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, bucket: str, key: str) -> Path:
        p = (self.root / bucket / key).resolve()
        if not str(p).startswith(str((self.root / bucket).resolve())):
            raise ValueError("object key escapes bucket root")
        return p

    def put_immutable(self, bucket: str, key: str, data: bytes) -> str:
        p = self._path(bucket, key)
        if p.exists():
            if sha256_bytes(p.read_bytes()) != sha256_bytes(data):
                raise ImmutableObjectError(f"refusing to overwrite immutable object {bucket}/{key}")
            return key
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        os.chmod(p, 0o444)   # read-only on disk
        return key

    def put(self, bucket: str, key: str, data: bytes) -> str:
        p = self._path(bucket, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            os.chmod(p, 0o644)
        p.write_bytes(data)
        return key

    def get(self, bucket: str, key: str) -> bytes:
        return self._path(bucket, key).read_bytes()

    def exists(self, bucket: str, key: str) -> bool:
        return self._path(bucket, key).exists()

    def list(self, bucket: str, prefix: str = "") -> list[str]:
        root = self.root / bucket
        if not root.exists():
            return []
        return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                      if p.is_file() and not p.name.endswith(".part") and str(p.relative_to(root)).startswith(prefix))


class S3ObjectStore:
    def __init__(self, settings: Settings):
        import boto3  # optional dependency: pip install irtcri[s3]

        self.sse = settings.s3_sse
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key.get_secret_value() if settings.s3_access_key else None,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value() if settings.s3_secret_key else None,
        )

    def exists(self, bucket: str, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError:
            return False

    def put_immutable(self, bucket: str, key: str, data: bytes) -> str:
        if self.exists(bucket, key):
            if sha256_bytes(self.get(bucket, key)) != sha256_bytes(data):
                raise ImmutableObjectError(f"refusing to overwrite immutable object {bucket}/{key}")
            return key
        # explicit checksum: required for buckets with Object Lock and verifies integrity in transit
        self.client.put_object(Bucket=bucket, Key=key, Body=data, ChecksumAlgorithm="SHA256",
                               Metadata={"sha256": sha256_bytes(data)}, **self._sse())
        return key

    def _sse(self) -> dict:
        return {"ServerSideEncryption": self.sse} if self.sse else {}

    def put(self, bucket: str, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=bucket, Key=key, Body=data, ChecksumAlgorithm="SHA256", **self._sse())
        return key

    def get(self, bucket: str, key: str) -> bytes:
        return self.client.get_object(Bucket=bucket, Key=key)["Body"].read()

    def list(self, bucket: str, prefix: str = "") -> list[str]:
        out = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            out += [o["Key"] for o in page.get("Contents", [])]
        return out


_store: ObjectStore | None = None


def get_object_store(settings: Settings | None = None) -> ObjectStore:
    global _store
    s = settings or get_settings()
    if _store is None:
        _store = S3ObjectStore(s) if s.object_store == "s3" else LocalObjectStore(s.local_store_root)
    return _store


def set_object_store(store: ObjectStore | None) -> None:
    """Override (tests)."""
    global _store
    _store = store


def raw_bucket() -> str:
    return get_settings().s3_bucket_raw


def derived_bucket() -> str:
    return get_settings().s3_bucket_derived
