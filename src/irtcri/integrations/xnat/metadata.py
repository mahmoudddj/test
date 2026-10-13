"""Metadata documents and searchable fields synchronised to XNAT.

Searchable XNAT custom fields carry only pseudonymous/technical identifiers. The complete
acquisition metadata (timestamps in UTC, camera, distance, protocol, checksums, QC) is stored
as a JSON resource so it round-trips exactly, independent of XNAT field-type limitations.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

SCHEMA = "irtcri.acquisition_metadata/1.0"


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def subject_fields(site_code: str) -> dict[str, str]:
    return {"site_code": site_code, "study": "irt_in_cri"}


def session_fields(ref) -> dict[str, str]:
    return {"site_code": ref.site_code, "episode_code": ref.episode_code, "acquisition_id": ref.acquisition_id,
            "acquisition_time_utc": ref.acquisition_time.strftime("%Y-%m-%dT%H:%M:%SZ")}


def acquisition_document(ref, metadata: dict[str, Any]) -> bytes:
    doc = {"schema": SCHEMA, "synthetic": bool(metadata.get("synthetic", False)),
           "image_id": ref.image_id, "modality": ref.modality, "site_code": ref.site_code,
           "study_id": ref.study_id, "catheter_episode_code": ref.episode_code,
           "acquisition_id": ref.acquisition_id, "acquisition_time_utc": ref.acquisition_time.isoformat(),
           "raw_sha256": ref.sha256, "file_format": ref.ext,
           "metadata": {k: _iso(v) for k, v in metadata.items()}}
    return json.dumps(doc, indent=2, sort_keys=True, default=str).encode()


def parse_acquisition_document(data: bytes) -> dict[str, Any]:
    doc = json.loads(data)
    if doc.get("schema", "").split("/")[0] != SCHEMA.split("/")[0]:
        raise ValueError("not an IRT-CRI acquisition metadata document")
    return doc
