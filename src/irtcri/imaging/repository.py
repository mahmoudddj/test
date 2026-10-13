"""Imaging repository abstraction.

``ImagingRepository`` is the interface; implementations:

* ``ObjectStoreImagingRepository`` - the existing write-once local/S3/MinIO storage (always on);
* ``irtcri.integrations.xnat.repository.XNATImagingRepository`` - optional (XNAT_ENABLED=true).

``ImagingService`` composes them (dual-write): originals always land in the object store (WORM copy,
ML cache, works offline at the bedside) and, when enabled, in XNAT (imaging archive for multicentre
workflows). Reads follow ``IMAGING_PRIMARY`` with checksum verification and fall back to the other
backend. A failed XNAT upload never loses data: the image stays in the object store with
``xnat_uri = NULL`` and is pushed later by ``irtcri xnat-push`` (same code path as the migration).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from irtcri.core.config import get_settings
from irtcri.core.provenance import sha256_bytes
from irtcri.imaging.storage import derived_bucket, get_object_store, raw_bucket

log = logging.getLogger("irtcri.imaging")


@dataclass(frozen=True)
class ImageRef:
    image_id: str | None
    modality: str
    sha256: str
    ext: str
    site_code: str
    study_id: str
    episode_code: str
    acquisition_id: str
    acquisition_time: datetime
    object_key: str | None = None
    xnat_uri: str | None = None


class ImagingRepository(Protocol):
    name: str

    def store_image(self, ref: ImageRef, data: bytes, metadata: dict[str, Any]) -> str: ...
    def get_image(self, location: str, sha256: str | None = None) -> bytes: ...
    def get_metadata(self, location: str) -> dict | None: ...
    def list_patient_images(self, study_id: str, site_code: str | None = None) -> list[str]: ...
    def list_catheter_episode_images(self, study_id: str, episode_code: str, site_code: str | None = None
                                     ) -> list[str]: ...
    def store_derived_resource(self, ref: ImageRef, kind: str, name: str, data: bytes,
                               content_type: str = "application/octet-stream") -> str: ...
    def get_derived_resource(self, location: str) -> bytes: ...
    def store_qc_result(self, ref: ImageRef, qc_doc: dict, visualization_png: bytes | None = None) -> str: ...
    def get_qc_result(self, ref: ImageRef) -> dict | None: ...


class ChecksumMismatch(RuntimeError):
    pass


class ObjectStoreImagingRepository:
    """The pre-existing storage layout, unchanged: raw/<site>/<study>/<episode>/<modality>/<sha>.<ext>."""

    name = "object_store"

    @staticmethod
    def raw_key(ref: ImageRef) -> str:
        return f"{ref.site_code}/{ref.study_id}/{ref.episode_code}/{ref.modality}/{ref.sha256}.{ref.ext}"

    def store_image(self, ref, data, metadata) -> str:
        return get_object_store().put_immutable(raw_bucket(), self.raw_key(ref), data)

    def get_image(self, location, sha256=None) -> bytes:
        data = get_object_store().get(raw_bucket(), location)
        if sha256 is not None and sha256_bytes(data) != sha256:
            raise ChecksumMismatch(f"checksum mismatch for object {location}")
        return data

    def get_metadata(self, location) -> dict | None:
        return {"object_key": location, "bucket": raw_bucket()}   # full metadata lives in PostgreSQL

    def list_patient_images(self, study_id, site_code=None) -> list[str]:
        return [k for k in get_object_store().list(raw_bucket(), f"{site_code}/" if site_code else "")
                if f"/{study_id}/" in f"/{k}"]

    def list_catheter_episode_images(self, study_id, episode_code, site_code=None) -> list[str]:
        return [k for k in self.list_patient_images(study_id, site_code) if f"/{episode_code}/" in k]

    def store_derived_resource(self, ref, kind, name, data, content_type="application/octet-stream") -> str:
        return get_object_store().put(derived_bucket(), f"{kind}/{ref.sha256}/{name}", data)

    def get_derived_resource(self, location) -> bytes:
        return get_object_store().get(derived_bucket(), location)

    def store_qc_result(self, ref, qc_doc, visualization_png=None) -> str:
        import json
        key = self.store_derived_resource(ref, "qc", "qc.json", json.dumps(qc_doc, default=str).encode())
        if visualization_png is not None:
            self.store_derived_resource(ref, "qc", "qc.png", visualization_png)
        return key

    def get_qc_result(self, ref) -> dict | None:
        import json
        try:
            return json.loads(self.get_derived_resource(f"qc/{ref.sha256}/qc.json"))
        except (FileNotFoundError, OSError, KeyError):
            return None


# ------------------------------------------------------------------ composition
_xnat_repo = None
_xnat_transport = None


def set_xnat_transport(transport) -> None:
    """Inject an httpx transport (tests / mocked XNAT)."""
    global _xnat_transport, _xnat_repo
    _xnat_transport, _xnat_repo = transport, None


def get_xnat_repository():
    global _xnat_repo
    s = get_settings()
    if not s.xnat_enabled:
        return None
    if _xnat_repo is None or _xnat_repo.cfg.url != s.xnat_url.rstrip("/") or \
            _xnat_repo.cfg.project_id != s.xnat_project_id:
        from irtcri.integrations.xnat.connection import XNATConfig, XNATConnection
        from irtcri.integrations.xnat.repository import XNATImagingRepository
        cfg = XNATConfig.from_settings(s)
        _xnat_repo = XNATImagingRepository(cfg, XNATConnection(cfg, transport=_xnat_transport))
    return _xnat_repo


#: derived kind -> publication group switched on/off by settings.xnat_publish
PUBLISH_GROUP = {"thermal_matrix": "companions", "preview": "companions", "metadata": "companions", "qc": "qc",
                 "gradcam": "gradcam", "prediction": "prediction", "annotation": "annotation",
                 "normalized": "derived", "registered": "derived", "segmentation": "derived"}


class ImagingService:
    def __init__(self, local: ObjectStoreImagingRepository | None = None, xnat=None, primary: str | None = None,
                 publish: list[str] | None = None):
        s = get_settings()
        self.local = local or ObjectStoreImagingRepository()
        self.xnat = xnat if xnat is not None else get_xnat_repository()
        self.primary = primary or s.imaging_primary
        self._publish = set(publish if publish is not None else s.xnat_publish)

    @property
    def xnat_enabled(self) -> bool:
        return self.xnat is not None

    def publishes(self, kind: str) -> bool:
        return self.xnat_enabled and kind in self._publish

    def store_image(self, ref: ImageRef, data: bytes, metadata: dict[str, Any],
                    skip_xnat: bool = False) -> dict[str, str | None]:
        out: dict[str, str | None] = {"object_store": self.local.store_image(ref, data, metadata), "xnat": None,
                                      "xnat_error": None}
        if self.xnat_enabled and not skip_xnat:
            try:
                out["xnat"] = self.xnat.store_image(ref, data, metadata)
            except Exception as e:   # never lose an acquisition because XNAT is unreachable
                out["xnat_error"] = f"{type(e).__name__}: {e}"
                log.warning("xnat upload deferred", extra={"fields": {"image_sha": ref.sha256[:12],
                                                                       "error": type(e).__name__}})
        return out

    def get_image(self, object_key: str | None, xnat_uri: str | None, sha256: str) -> bytes:
        order = [("object_store", object_key), ("xnat", xnat_uri)]
        if self.primary == "xnat":
            order.reverse()
        errors = []
        for backend, loc in order:
            if not loc or (backend == "xnat" and not self.xnat_enabled):
                continue
            repo = self.local if backend == "object_store" else self.xnat
            try:
                return repo.get_image(loc, sha256)
            except Exception as e:
                errors.append(f"{backend}: {type(e).__name__}")
        raise FileNotFoundError(f"image {sha256[:12]} not retrievable ({'; '.join(errors) or 'no location'})")

    def store_derived(self, ref: ImageRef, kind: str, name: str, data: bytes, local: bool = True,
                      content_type: str = "application/octet-stream") -> dict[str, str | None]:
        out = {"object_store": self.local.store_derived_resource(ref, kind, name, data) if local else None,
               "xnat": None}
        if self.publishes(PUBLISH_GROUP.get(kind, "derived")):
            out["xnat"] = self.xnat.store_derived_resource(ref, kind, name, data, content_type)
        return out

    def store_qc_result(self, ref: ImageRef, qc_doc: dict, png: bytes | None = None) -> dict[str, str | None]:
        out = {"object_store": self.local.store_qc_result(ref, qc_doc, png), "xnat": None}
        if self.publishes("qc"):
            out["xnat"] = self.xnat.store_qc_result(ref, qc_doc, png)
        return out


def get_imaging_service() -> ImagingService:
    return ImagingService()


def image_ref(session, row, modality: str) -> ImageRef:
    from irtcri.db.models import CatheterEpisode, Patient, Site
    ep = session.get(CatheterEpisode, row.episode_id)
    pat = session.get(Patient, row.patient_id)
    site = session.get(Site, row.site_id)
    from irtcri.integrations.xnat.mapping import acquisition_label
    return ImageRef(image_id=row.id, modality=modality, sha256=row.raw_sha256, ext=row.file_format or "bin",
                    site_code=site.site_code, study_id=pat.study_id, episode_code=ep.episode_code,
                    acquisition_id=row.acquisition_id or acquisition_label(ep.episode_code, row.acquisition_time),
                    acquisition_time=row.acquisition_time, object_key=row.raw_object_key, xnat_uri=row.xnat_uri)
