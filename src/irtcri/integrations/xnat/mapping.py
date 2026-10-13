"""Mapping between the IRT-CRI study model and XNAT.

    IRT-in-CRI study            -> XNAT project   (XNAT_PROJECT_ID)
    pseudonymous participant    -> XNAT subject   (label = Study ID, e.g. S01-0001)
    acquisition time point      -> XNAT imaging session (label = acquisition_id
                                   = <episode_code>_<YYYYmmddTHHMM>Z, UTC)
    thermal / RGB source file   -> XNAT scan (ID T<sha10> / R<sha10>; type THERMAL / RGB)
                                   with resource RAW (thermal original) or RGB (RGB original)
    derived artefacts           -> scan resources THERMAL_MATRIX, PREVIEW;
                                   session resources QC, DERIVED, PREDICTIONS, ANNOTATIONS, METADATA

The catheter episode is NOT forced into an XNAT construct: it stays in PostgreSQL and is carried as
explicit metadata (session field ``episode_code`` + METADATA JSON). One subject may therefore have
sessions from several catheter episodes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from irtcri.hub.schemas import STUDY_ID_RE

LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

RAW_RESOURCE = {"thermal": "RAW", "rgb": "RGB"}
SCAN_TYPE = {"thermal": "THERMAL", "rgb": "RGB"}
DERIVED_KINDS: dict[str, tuple[str, str]] = {   # kind -> (resource label, level)
    "thermal_matrix": ("THERMAL_MATRIX", "scan"),
    "preview": ("PREVIEW", "scan"),
    "qc": ("QC", "session"),
    "gradcam": ("DERIVED", "session"),
    "normalized": ("DERIVED", "session"),
    "registered": ("DERIVED", "session"),
    "segmentation": ("DERIVED", "session"),
    "prediction": ("PREDICTIONS", "session"),
    "annotation": ("ANNOTATIONS", "session"),
    "metadata": ("METADATA", "session"),
}


def acquisition_label(episode_code: str, t: datetime) -> str:
    label = f"{episode_code}_{t.astimezone(timezone.utc):%Y%m%dT%H%M}Z"
    if not LABEL_RE.match(label):
        raise ValueError(f"invalid XNAT session label {label!r}")
    return label


def subject_label(study_id: str) -> str:
    """Only pseudonymous Study IDs may become XNAT subject labels (guards against PHI)."""
    if not STUDY_ID_RE.match(study_id):
        raise ValueError(f"{study_id!r} is not a pseudonymous Study ID; refusing to create an XNAT subject")
    return study_id


def scan_id(modality: str, sha256: str) -> str:
    return ("T" if modality == "thermal" else "R") + sha256[:10]


@dataclass(frozen=True)
class XNATLocation:
    project: str
    subject: str
    session: str
    resource: str
    name: str
    scan: str | None = None

    @property
    def uri(self) -> str:
        mid = f"scans/{self.scan}/" if self.scan else ""
        return f"xnat://{self.project}/{self.subject}/{self.session}/{mid}resources/{self.resource}/files/{self.name}"

    @property
    def parent_path(self) -> str:
        base = f"/data/projects/{self.project}/subjects/{self.subject}/experiments/{self.session}"
        return f"{base}/scans/{self.scan}" if self.scan else base

    @property
    def rest_path(self) -> str:
        return f"{self.parent_path}/resources/{self.resource}/files/{self.name}"

    @classmethod
    def parse(cls, uri: str) -> "XNATLocation":
        m = re.match(r"^xnat://([^/]+)/([^/]+)/([^/]+)/(?:scans/([^/]+)/)?resources/([^/]+)/files/(.+)$", uri)
        if not m:
            raise ValueError(f"not an XNAT URI: {uri}")
        p, s, e, scan, res, name = m.groups()
        return cls(p, s, e, res, name, scan)
