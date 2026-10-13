"""Synchronisation between the Data Hub and XNAT.

* ``pull_from_xnat``  - register images that sites uploaded directly to XNAT (XNAT-first workflow);
* ``migrate_images_to_xnat`` - push existing hub images to XNAT (DRY-RUN by default), verifying
  checksums after upload. Source objects are NEVER deleted.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.provenance import sha256_bytes
from irtcri.core.security import Principal
from irtcri.core.timeutils import parse_timestamp, utcnow
from irtcri.db.audit import audit
from irtcri.db.models import CatheterEpisode, RGBImage, ThermalImage
from irtcri.imaging.ingest import (
    IngestError,
    decode,
    ingest_image,
    publish_companions,
    raw_bytes,
    record_external,
)
from irtcri.imaging.repository import ImagingService, get_imaging_service, image_ref
from irtcri.integrations.xnat import sessions
from irtcri.integrations.xnat.mapping import XNATLocation

MODALITY_BY_RESOURCE = {"RAW": "thermal", "RGB": "rgb"}
ROW_META = ("view", "timepoint_label", "distance_cm", "camera_make", "camera_model", "device_id", "operator_id",
            "acquisition_protocol_version", "emissivity", "reflected_temperature_c", "ambient_temperature_c",
            "relative_humidity_pct", "calibration_metadata")


def _row_metadata(row) -> dict[str, Any]:
    out = {k: getattr(row, k, None) for k in ROW_META if getattr(row, k, None) is not None}
    out.update(qc_status=row.qc_status, qc_config_version=row.qc_config_version,
               synthetic=bool((row.extra_metadata or {}).get("synthetic")), hub_image_id=row.id)
    return out


def _session_field_values(conn, project: str, subject: str, label: str) -> dict[str, str]:
    """Custom fields of an XNAT experiment (for sessions created directly in XNAT by sites)."""
    try:
        js = conn.get_json(sessions.session_path(project, subject, label))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for item in js.get("items", []):
        for child in item.get("children", []):
            if child.get("field", "").endswith("fields/field"):
                for f in child.get("items", []):
                    df = f.get("data_fields", {})
                    if "name" in df:
                        out[df["name"].lower()] = df.get("field")
    return out


def pull_from_xnat(session: Session, who: Principal, dry_run: bool = True,
                   service: ImagingService | None = None) -> dict:
    service = service or get_imaging_service()
    repo = service.xnat
    if repo is None:
        raise RuntimeError("XNAT is not enabled (XNAT_ENABLED=false)")
    known = {u for (u,) in session.execute(select(ThermalImage.xnat_uri).where(ThermalImage.xnat_uri.is_not(None)))}
    known |= {u for (u,) in session.execute(select(RGBImage.xnat_uri).where(RGBImage.xnat_uri.is_not(None)))}
    report: dict[str, Any] = {"dry_run": dry_run, "new": [], "registered": 0, "skipped_known": 0, "errors": []}
    for s in sessions.list_sessions(repo.conn, repo.cfg.project_id):
        subject, label = s["subject_label"], s["label"]
        fields = None
        for uri in repo._session_raw_uris(subject, label):
            if uri in known:
                report["skipped_known"] += 1
                continue
            loc = XNATLocation.parse(uri)
            doc = repo.get_metadata(uri)
            if doc:
                meta = dict(doc.get("metadata", {}))
                episode_code, acq = doc["catheter_episode_code"], doc["acquisition_time_utc"]
            else:
                fields = fields if fields is not None else _session_field_values(repo.conn, loc.project, subject,
                                                                                  label)
                meta = {}
                episode_code, acq = fields.get("episode_code"), fields.get("acquisition_time_utc")
            entry = {"uri": uri, "study_id": subject, "episode_code": episode_code, "acquisition_time_utc": acq}
            if not episode_code or not acq:
                report["errors"].append({**entry, "error": "missing episode_code / acquisition_time_utc metadata "
                                                           "(set the XNAT session fields; nothing is guessed)"})
                continue
            report["new"].append(entry)
            if dry_run:
                continue
            try:
                ep = session.scalar(select(CatheterEpisode).where(CatheterEpisode.episode_code == episode_code))
                if ep is None:
                    raise IngestError(f"unknown episode {episode_code}")
                data = repo.get_image(uri)
                m = {k: v for k, v in meta.items() if k in ROW_META}
                m.update(episode_id=ep.id, study_id=subject,
                         acquisition_time=parse_timestamp(acq, "UTC", "acquisition_time"))
                with session.begin_nested():
                    ingest_image(session, who, MODALITY_BY_RESOURCE[loc.resource], data, loc.name, m,
                                 channel="xnat", existing_xnat_uri=uri)
                report["registered"] += 1
            except Exception as e:
                report["errors"].append({**entry, "error": f"{type(e).__name__}: {e}"})
    if not dry_run:
        session.commit()
    return report


def migrate_images_to_xnat(session: Session, who: Principal, dry_run: bool = True, limit: int | None = None,
                           service: ImagingService | None = None) -> dict:
    """Push hub images that are not yet in XNAT. Verifies the SHA-256 of the copy read back from XNAT.

    Preserves study_id, catheter episode, acquisition time (UTC), acquisition metadata, checksums and
    provenance (METADATA JSON + external_resource rows + audit events). Never deletes the source.
    """
    service = service or get_imaging_service()
    report: dict[str, Any] = {"dry_run": dry_run, "started": utcnow().isoformat(), "planned": [], "migrated": 0,
                              "verified": 0, "failed": []}
    if not service.xnat_enabled:
        raise RuntimeError("XNAT is not enabled (XNAT_ENABLED=false)")
    for Model, modality in ((ThermalImage, "thermal"), (RGBImage, "rgb")):
        rows = session.scalars(select(Model).where(Model.xnat_uri.is_(None)).order_by(Model.acquisition_time)).all()
        for row in rows:
            if limit is not None and len(report["planned"]) >= limit:
                break
            if not row.acquisition_id:
                from irtcri.imaging.ingest import _acquisition_id
                ep = session.get(CatheterEpisode, row.episode_id)
                row.acquisition_id = _acquisition_id(session, modality, ep, row.acquisition_time, row.view)
            ref = image_ref(session, row, modality)
            target = service.xnat.raw_location(ref).uri
            report["planned"].append({"image_id": row.id, "modality": modality, "study_id": ref.study_id,
                                      "episode_code": ref.episode_code, "acquisition_id": ref.acquisition_id,
                                      "acquisition_time_utc": ref.acquisition_time.isoformat(),
                                      "sha256": row.raw_sha256, "size_bytes": row.raw_size_bytes,
                                      "voided": row.is_voided, "target": target})
            if dry_run:
                continue
            try:
                data = raw_bytes(row)                                   # verified against the DB checksum
                uri = service.xnat.store_image(ref, data, _row_metadata(row))
                back = service.xnat.get_image(uri)
                if sha256_bytes(back) != row.raw_sha256:
                    raise RuntimeError("checksum mismatch after upload")
                report["verified"] += 1
                row.xnat_uri, row.xnat_synced_time = uri, utcnow()
                record_external(session, "xnat", Model.__tablename__, row.id, "raw", uri, row.raw_sha256)
                if modality == "thermal":
                    dec, _ = decode("thermal", data, row.original_filename or "x." + (row.file_format or "bin"))
                    if dec is not None and dec.temperature_c is not None:
                        publish_companions(session, service, image_ref(session, row, modality), row, dec)
                audit(session, who, "xnat_migrate", Model.__tablename__, row.id, after={"xnat_uri": uri},
                      reason="copy to XNAT; source retained")
                session.commit()
                report["migrated"] += 1
            except Exception as e:
                session.rollback()
                report["failed"].append({"image_id": row.id, "error": f"{type(e).__name__}: {e}"})
    if dry_run:
        session.rollback()     # acquisition_id back-fill is applied only on a real run
    report["finished"] = utcnow().isoformat()
    return report


