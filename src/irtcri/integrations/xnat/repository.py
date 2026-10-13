"""XNAT implementation of the ImagingRepository interface."""

from __future__ import annotations

import json
from typing import Any

from irtcri.integrations.xnat import metadata as md
from irtcri.integrations.xnat import resources, sessions, subjects
from irtcri.integrations.xnat.connection import XNATConfig, XNATConnection
from irtcri.integrations.xnat.download import download_file, download_verified
from irtcri.integrations.xnat.mapping import (
    DERIVED_KINDS,
    RAW_RESOURCE,
    SCAN_TYPE,
    XNATLocation,
    scan_id,
    subject_label,
)
from irtcri.integrations.xnat.projects import get_project
from irtcri.integrations.xnat.upload import upload_file


class XNATImagingRepository:
    name = "xnat"

    def __init__(self, cfg: XNATConfig, conn: XNATConnection | None = None):
        self.cfg = cfg
        self.conn = conn or XNATConnection(cfg)
        self._known_subjects: set[str] = set()
        self._known_sessions: set[str] = set()
        self._project_checked = False

    # ------------------------------------------------------------- structure
    def check_project(self) -> dict:
        info = get_project(self.conn, self.cfg.project_id)
        self._project_checked = True
        return info

    def _ensure_session(self, ref) -> None:
        if not self._project_checked:
            self.check_project()
        subj = subject_label(ref.study_id)
        if subj not in self._known_subjects:
            subjects.ensure_subject(self.conn, self.cfg.project_id, subj, md.subject_fields(ref.site_code))
            self._known_subjects.add(subj)
        if ref.acquisition_id not in self._known_sessions:
            sessions.ensure_session(self.conn, self.cfg.project_id, subj, ref.acquisition_id,
                                    self.cfg.session_xsi_type, ref.acquisition_time, md.session_fields(ref))
            self._known_sessions.add(ref.acquisition_id)

    def raw_location(self, ref) -> XNATLocation:
        return XNATLocation(self.cfg.project_id, ref.study_id, ref.acquisition_id, RAW_RESOURCE[ref.modality],
                            f"{ref.modality}_{ref.sha256[:12]}.{ref.ext}", scan_id(ref.modality, ref.sha256))

    # ------------------------------------------------------------ interface
    def store_image(self, ref, data: bytes, metadata: dict[str, Any]) -> str:
        self._ensure_session(ref)
        loc = self.raw_location(ref)
        sessions.ensure_scan(self.conn, loc.project, loc.subject, loc.session, loc.scan, self.cfg.scan_xsi_type,
                             SCAN_TYPE[ref.modality], str(metadata.get("view") or ""))
        resources.ensure_resource(self.conn, loc.parent_path, loc.resource, ref.ext, "RAW_ORIGINAL")
        upload_file(self.conn, loc.parent_path, loc.resource, loc.name, data)
        # full acquisition metadata (round-trips exactly); per-scan file in the session METADATA resource
        self._put_session_file(ref, "metadata", f"{loc.scan}_acquisition.json",
                               md.acquisition_document(ref, metadata), "application/json", overwrite=True)
        return loc.uri

    def get_image(self, location: str, sha256: str | None = None) -> bytes:
        return download_verified(self.conn, XNATLocation.parse(location).rest_path, sha256)

    def get_metadata(self, location: str) -> dict | None:
        loc = XNATLocation.parse(location)
        if loc.scan is None:
            return None
        meta = XNATLocation(loc.project, loc.subject, loc.session, "METADATA", f"{loc.scan}_acquisition.json")
        try:
            return md.parse_acquisition_document(download_file(self.conn, meta.rest_path))
        except Exception:
            return None

    def list_patient_images(self, study_id: str, site_code: str | None = None) -> list[str]:
        out = []
        for s in sessions.list_sessions(self.conn, self.cfg.project_id, study_id):
            out += self._session_raw_uris(study_id, s["label"])
        return out

    def list_catheter_episode_images(self, study_id: str, episode_code: str, site_code: str | None = None
                                     ) -> list[str]:
        out = []
        for s in sessions.list_sessions(self.conn, self.cfg.project_id, study_id):
            if s["label"].startswith(episode_code + "_"):
                out += self._session_raw_uris(study_id, s["label"])
        return out

    def _session_raw_uris(self, subject: str, session: str) -> list[str]:
        out = []
        for sc in sessions.list_scans(self.conn, self.cfg.project_id, subject, session):
            sid = str(sc.get("ID"))
            parent = XNATLocation(self.cfg.project_id, subject, session, "", "", sid).parent_path
            for res in ("RAW", "RGB"):
                for f in resources.list_files(self.conn, parent, res):
                    out.append(XNATLocation(self.cfg.project_id, subject, session, res, f["Name"], sid).uri)
        return out

    def store_derived_resource(self, ref, kind: str, name: str, data: bytes,
                               content_type: str = "application/octet-stream") -> str:
        return self._put_session_file(ref, kind, name, data, content_type, overwrite=True)

    def get_derived_resource(self, location: str) -> bytes:
        return download_file(self.conn, XNATLocation.parse(location).rest_path)

    def store_qc_result(self, ref, qc_doc: dict, visualization_png: bytes | None = None) -> str:
        scan = scan_id(ref.modality, ref.sha256)
        uri = self._put_session_file(ref, "qc", f"{scan}_qc.json", json.dumps(qc_doc, indent=2, default=str).encode(),
                                     "application/json", overwrite=True)
        if visualization_png is not None:
            self._put_session_file(ref, "qc", f"{scan}_qc.png", visualization_png, "image/png", overwrite=True)
        return uri

    def get_qc_result(self, ref) -> dict | None:
        loc = XNATLocation(self.cfg.project_id, ref.study_id, ref.acquisition_id, "QC",
                           f"{scan_id(ref.modality, ref.sha256)}_qc.json")
        try:
            return json.loads(download_file(self.conn, loc.rest_path))
        except Exception:
            return None

    # ---------------------------------------------------------------- helpers
    def _put_session_file(self, ref, kind: str, name: str, data: bytes, content_type: str,
                          overwrite: bool) -> str:
        label, level = DERIVED_KINDS[kind]
        self._ensure_session(ref)
        loc = XNATLocation(self.cfg.project_id, ref.study_id, ref.acquisition_id, label, name,
                           scan_id(ref.modality, ref.sha256) if level == "scan" else None)
        resources.ensure_resource(self.conn, loc.parent_path, label, name.rsplit(".", 1)[-1], kind.upper())
        upload_file(self.conn, loc.parent_path, label, name, data, content_type, overwrite=overwrite)
        return loc.uri
