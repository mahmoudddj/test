"""Provenance helpers: checksums, code version, environment fingerprint."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from irtcri import __version__


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj).encode())


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def git_commit(short: bool = False) -> str | None:
    """Current git commit (with ``-dirty`` suffix if the tree has changes).

    Inside containers (no .git) the commit baked in at build time (IRTCRI_GIT_COMMIT) is used.
    """
    import os
    baked = os.environ.get("IRTCRI_GIT_COMMIT")
    try:
        root = Path(__file__).resolve().parents[3]
        args = ["git", "-C", str(root), "rev-parse", "HEAD"]
        commit = subprocess.run(args, capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if short:
            commit = commit[:10]
        return commit + ("-dirty" if dirty else "")
    except Exception:
        return baked if baked and baked != "unknown" else None


KEY_PACKAGES = ("numpy", "pandas", "scipy", "scikit-learn", "statsmodels", "torch", "torchvision", "pyarrow",
                "sqlalchemy", "pydantic", "fastapi", "pillow", "matplotlib")


def package_versions() -> dict[str, str | None]:
    from importlib import metadata
    out = {}
    for p in KEY_PACKAGES:
        try:
            out[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            out[p] = None
    return out


def code_version() -> dict[str, Any]:
    pk = package_versions()
    return {
        "package_version": __version__,
        "git_commit": git_commit(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": pk,
        "environment_sha256": sha256_json(pk),
    }
