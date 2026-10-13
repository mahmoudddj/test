"""Image ingestion service shared by the upload API, watch-folder and camera adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.config import get_settings
from irtcri.core.provenance import sha256_bytes
from irtcri.core.security import Principal
from irtcri.core.timeutils import ensure_aware, utcnow
from irtcri.db.audit import audit, row_snapshot
from irtcri.db.models import CatheterEpisode, ImagePair, Patient, RGBImage, Site, ThermalImage
from irtcri.imaging.qc import QCContext, QCResult, load_qc_config, run_qc
from irtcri.imaging.readers import DecodedImage, ImageReadError, read_rgb, read_thermal
from irtcri.imaging.storage import get_object_store, raw_bucket

PAIRING_WINDOW = timedelta(minutes=2)

THERMAL_META_FIELDS = ("emissivity", "reflected_temperature_c", "ambient_temperature_c",
                       "relative_humidity_pct", "calibration_metadata")
COMMON_META_FIELDS = ("timepoint_label", "view", "distance_cm", "camera_make", "camera_model", "device_id",
                      "operator_id", "acquisition_protocol_version", "extra_metadata")


class IngestError(ValueError):
    pass


@dataclass
class IngestResult:
    image: ThermalImage | RGBImage
    qc: QCResult
    pair: ImagePair | None
    already_present: bool = False


def _model(modality: str):
    if modality == "thermal":
        return ThermalImage
    if modality == "rgb":
        return RGBImage
    raise IngestError(f"unknown modality {modality}")


def resolve_episode(session: Session, episode_ref: str) -> CatheterEpisode:
    ep = session.get(CatheterEpisode, episode_ref) or session.scalar(
        select(CatheterEpisode).where(CatheterEpisode.episode_code == episode_ref))
    if ep is None or ep.is_voided:
        raise IngestError(f"unknown catheter episode '{episode_ref}'")
    return ep


def build_qc_context(session: Session, modality: str, episode: CatheterEpisode, sha: str,
                     site: Site, exclude_id: str | None = None) -> QCContext:
    Model = _model(modality)
    dups = session.scalars(select(Model.id).where(Model.raw_sha256 == sha, Model.is_voided.is_(False))).all()
    others = session.execute(
        select(Model.id, Model.acquisition_time, Model.view, Model.qc_metrics)
        .where(Model.episode_id == episode.id, Model.is_voided.is_(False))).all()
    return QCContext(
        now=utcnow(), site_timezone=site.timezone, insertion_time=episode.insertion_time,
        removal_time=episode.removal_time,
        exact_duplicate_ids=[d for d in dups if d != exclude_id],
        episode_images=[(i, t, v, (m or {}).get("dhash")) for i, t, v, m in others if i != exclude_id],
    )


def decode(modality: str, data: bytes, filename: str, reader_options: dict | None = None) -> tuple[
        DecodedImage | None, str | None]:
    try:
        if modality == "thermal":
            return read_thermal(data, filename, **(reader_options or {})), None
        return read_rgb(data, filename), None
    except ImageReadError as e:
        return None, str(e)


def ingest_image(session: Session, who: Principal, modality: str, data: bytes, filename: str,
                 meta: dict[str, Any], channel: str = "upload",
                 reader_options: dict | None = None) -> IngestResult:
    """Store the raw file immutably, run QC, register metadata. Never discards data."""
    Model = _model(modality)
    episode = resolve_episode(session, meta["episode_id"])
    patient = session.get(Patient, episode.patient_id)
    site = session.get(Site, episode.site_id)
    if not who.can_access_site(site.id):
        raise PermissionError("not authorised for this site")
    if meta.get("study_id") and meta["study_id"] != patient.study_id:
        raise IngestError(f"study_id '{meta['study_id']}' does not match episode's patient '{patient.study_id}'")
    acq = ensure_aware(meta.get("acquisition_time"), "acquisition_time")
    if acq is None:
        raise IngestError("acquisition_time is required (timezone-aware)")

    sha = sha256_bytes(data)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    from irtcri.hub.services import ensure_not_withdrawn
    ensure_not_withdrawn(patient, acq)
    # The uploaded filename may contain names/MRNs (camera defaults, manual renaming). It is never
    # stored: objects are keyed by Study ID + checksum; only a hash of the original name is kept.
    safe_name = f"{modality}_{sha[:12]}.{ext}"
    name_hash = sha256_bytes(filename.encode())

    # Same bytes already registered for this same episode -> idempotent re-ingestion (e.g. watch folder rescans).
    existing = session.scalar(select(Model).where(Model.raw_sha256 == sha, Model.episode_id == episode.id,
                                                  Model.acquisition_time == acq, Model.is_voided.is_(False)))
    if existing is not None:
        return IngestResult(existing, QCResult(), None, already_present=True)

    key = f"{site.site_code}/{patient.study_id}/{episode.episode_code}/{modality}/{sha}.{ext}"
    get_object_store().put_immutable(raw_bucket(), key, data)

    dec, err = decode(modality, data, filename, reader_options)
    qc_meta = {**meta, "study_id": patient.study_id, "episode_id": episode.id, "acquisition_time": acq}
    ctx = build_qc_context(session, modality, episode, sha, site)
    qc = run_qc(modality, dec, err, qc_meta, ctx, ext, len(data))

    row = Model(
        patient_id=patient.id, episode_id=episode.id, site_id=site.id, acquisition_time=acq,
        exif_time=None, ingestion_channel=channel, original_filename=safe_name, file_format=ext,
        raw_object_key=key, raw_sha256=sha, raw_size_bytes=len(data),
        width=dec.width if dec else None, height=dec.height if dec else None,
        qc_status=qc.status, qc_reasons=qc.reasons, qc_metrics=qc.metrics, qc_config_version=qc.config_version,
        qc_time=utcnow(), duplicate_of_id=ctx.exact_duplicate_ids[0] if ctx.exact_duplicate_ids else None,
        source=meta.get("source", "manual" if channel == "upload" else "device"), recorded_by=who.user_id,
        acquisition_protocol_version=meta.get("acquisition_protocol_version")
        or get_settings().acquisition_protocol_version,
    )
    for f in COMMON_META_FIELDS:
        if f in meta and meta[f] is not None and f != "acquisition_protocol_version":
            setattr(row, f, meta[f])
    row.extra_metadata = {**(row.extra_metadata or {}), "original_filename_sha256": name_hash}
    if dec is not None and dec.exif_time is not None:
        from zoneinfo import ZoneInfo
        row.exif_time = dec.exif_time.replace(tzinfo=ZoneInfo(site.timezone))
    if modality == "thermal":
        for f in THERMAL_META_FIELDS:
            if meta.get(f) is not None:
                setattr(row, f, meta[f])
        row.is_radiometric = bool(dec and dec.is_radiometric)
        row.temperature_unit = "degC" if row.is_radiometric else None
        row.t_min, row.t_max, row.t_mean = (qc.metrics.get("t_min"), qc.metrics.get("t_max"),
                                            qc.metrics.get("t_mean"))
    else:
        row.blur_score = qc.metrics.get("blur_score")
    session.add(row)
    session.flush()
    audit(session, who, "create", Model.__tablename__, row.id, after=row_snapshot(row),
          reason=f"image ingestion via {channel}")
    pair = auto_pair(session, row, modality)
    return IngestResult(row, qc, pair)


def auto_pair(session: Session, row, modality: str) -> ImagePair | None:
    """Pair thermal+RGB images of the same episode and view captured within PAIRING_WINDOW."""
    Other = RGBImage if modality == "thermal" else ThermalImage
    cands = session.scalars(select(Other).where(
        Other.episode_id == row.episode_id, Other.view == row.view, Other.is_voided.is_(False),
        Other.acquisition_time >= row.acquisition_time - PAIRING_WINDOW,
        Other.acquisition_time <= row.acquisition_time + PAIRING_WINDOW)).all()
    if not cands:
        return None
    best = min(cands, key=lambda c: abs((c.acquisition_time - row.acquisition_time).total_seconds()))
    th, rgb = (row, best) if modality == "thermal" else (best, row)
    if session.scalar(select(ImagePair).where(
            (ImagePair.thermal_image_id == th.id) | (ImagePair.rgb_image_id == rgb.id))):
        return None
    pair = ImagePair(thermal_image_id=th.id, rgb_image_id=rgb.id, pairing_method="time_window",
                     time_difference_s=abs((th.acquisition_time - rgb.acquisition_time).total_seconds()),
                     registration_method="none")
    session.add(pair)
    session.flush()
    return pair


def rerun_qc(session: Session, who: Principal, modality: str, image_id: str,
             config: dict | None = None, reader_options: dict | None = None) -> QCResult:
    """Re-run QC (e.g. after a threshold change). Previous result is preserved in the audit trail."""
    Model = _model(modality)
    row = session.get(Model, image_id)
    data = get_object_store().get(raw_bucket(), row.raw_object_key)
    dec, err = decode(modality, data, row.original_filename or row.raw_object_key, reader_options)
    episode = session.get(CatheterEpisode, row.episode_id)
    site = session.get(Site, row.site_id)
    patient = session.get(Patient, row.patient_id)
    ctx = build_qc_context(session, modality, episode, row.raw_sha256, site, exclude_id=row.id)
    # only earlier-registered byte-identical images make this one a duplicate
    ctx.exact_duplicate_ids = [i for i in ctx.exact_duplicate_ids
                               if session.get(Model, i).recorded_time <= row.recorded_time]
    meta = {c: getattr(row, c, None) for c in COMMON_META_FIELDS + (THERMAL_META_FIELDS if modality == "thermal"
                                                                       else ())}
    meta.update({"study_id": patient.study_id, "episode_id": row.episode_id, "acquisition_time": row.acquisition_time})
    qc = run_qc(modality, dec, err, meta, ctx, row.file_format or "", row.raw_size_bytes or 0,
                config or load_qc_config())
    before = {"qc_status": row.qc_status, "qc_reasons": row.qc_reasons, "qc_config_version": row.qc_config_version}
    row.qc_status, row.qc_reasons, row.qc_metrics = qc.status, qc.reasons, qc.metrics
    row.qc_config_version, row.qc_time = qc.config_version, utcnow()
    audit(session, who, "qc_rerun", Model.__tablename__, row.id, before=before,
          after={"qc_status": qc.status, "qc_reasons": qc.reasons, "qc_config_version": qc.config_version})
    return qc


def effective_qc_status(row) -> str:
    """Human override (with reason) takes precedence for dataset inclusion; automated result is kept."""
    return row.qc_override_status or row.qc_status


def load_temperature(row: ThermalImage, reader_options: dict | None = None) -> DecodedImage:
    data = get_object_store().get(raw_bucket(), row.raw_object_key)
    return read_thermal(data, row.original_filename or row.raw_object_key, **(reader_options or {}))


def load_rgb(row: RGBImage) -> DecodedImage:
    data = get_object_store().get(raw_bucket(), row.raw_object_key)
    return read_rgb(data, row.original_filename or row.raw_object_key)


def acquisition_meta_from_sidecar(sidecar: dict, tz_name: str) -> dict[str, Any]:
    """Normalise metadata provided in sidecar JSON (watch folder / camera export)."""
    from irtcri.core.timeutils import parse_timestamp
    m = dict(sidecar)
    m["acquisition_time"] = parse_timestamp(m.get("acquisition_time"), tz_name, "acquisition_time")
    for f in ("distance_cm", "ambient_temperature_c", "emissivity", "relative_humidity_pct",
              "reflected_temperature_c"):
        if m.get(f) not in (None, ""):
            m[f] = float(m[f])
    return m


