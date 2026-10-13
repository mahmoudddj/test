"""Write-back of derived results to the imaging repository (XNAT when enabled).

The model/experiment registry of record remains the run directory / model card (and MLflow when
configured). XNAT receives *copies* of prediction metadata and imaging artefacts so imaging
reviewers can see them next to the images; every publication is recorded in ``external_resource``.
"""

from __future__ import annotations

import json

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.provenance import git_commit, sha256_bytes
from irtcri.db.models import Adjudication, Annotation, ModelPrediction, ModelVersion, ThermalImage
from irtcri.imaging.ingest import load_temperature, record_external
from irtcri.imaging.repository import ImagingService, get_imaging_service, image_ref
from irtcri.integrations.xnat.mapping import scan_id


def publish_prediction(session: Session, pred: ModelPrediction, mv: ModelVersion, card: dict | None = None,
                       cam: np.ndarray | None = None, service: ImagingService | None = None) -> list[str]:
    service = service or get_imaging_service()
    if not pred.thermal_image_id or not service.publishes("prediction"):
        return []
    row = session.get(ThermalImage, pred.thermal_image_id)
    if row is None or not row.xnat_uri:
        return []
    ref = image_ref(session, row, "thermal")
    doc = {"schema": "irtcri.model_prediction/1.0", "prediction_id": pred.id, "model_id": mv.id,
           "model_name": mv.name, "model_version": mv.version, "model_status": mv.status,
           "dataset_version": mv.training_dataset_version, "prediction_timestamp": pred.prediction_time.isoformat(),
           "computed_time": pred.computed_time.isoformat() if pred.computed_time else None, "mode": pred.mode,
           "predicted_probability": pred.probability, "operating_threshold": pred.threshold,
           "predicted_positive": pred.predicted_positive, "git_commit": mv.code_version or git_commit(),
           "model_registry": "IRT-CRI model card / MLflow (authoritative)", "artifact_uri": mv.artifact_uri,
           "disclaimer": "Investigational research output; not a diagnosis."}
    data = json.dumps(doc, indent=2).encode()
    uris = []
    name = f"{scan_id('thermal', ref.sha256)}_{mv.name}_{mv.version}_prediction.json"
    loc = service.store_derived(ref, "prediction", name, data, local=False, content_type="application/json")
    if loc["xnat"]:
        record_external(session, "xnat", "model_prediction", pred.id, "prediction", loc["xnat"], sha256_bytes(data))
        uris.append(loc["xnat"])
    if cam is not None and service.publishes("gradcam"):
        from irtcri.imaging.derive import unletterbox
        from irtcri.imaging.render import thermal_png
        t = load_temperature(row).temperature_c
        png, _ = thermal_png(t, overlay=unletterbox(cam.astype(np.float32), t.shape))
        loc = service.store_derived(ref, "gradcam", name.replace("_prediction.json", "_gradcam.png"), png,
                                    local=False, content_type="image/png")
        if loc["xnat"]:
            record_external(session, "xnat", "model_prediction", pred.id, "gradcam", loc["xnat"], sha256_bytes(png))
            uris.append(loc["xnat"])
    return uris


def publish_annotations(session: Session, image_ids: list[str] | None = None,
                        service: ImagingService | None = None) -> int:
    """Publish submitted reader annotations (ROIs/labels) + current adjudication for each image.

    Annotator identity is reduced to the reader role; blinding is unaffected because XNAT is not
    used for reading (readers work in the platform's blinded tool).
    """
    service = service or get_imaging_service()
    if not service.publishes("annotation"):
        return 0
    q = select(Annotation.thermal_image_id).where(Annotation.status == "submitted").distinct()
    ids = image_ids or list(session.scalars(q))
    n = 0
    for img_id in ids:
        row = session.get(ThermalImage, img_id)
        if row is None or not row.xnat_uri:
            continue
        anns = session.scalars(select(Annotation).where(Annotation.thermal_image_id == img_id,
                                                        Annotation.status == "submitted")).all()
        adj = session.scalar(select(Adjudication).where(Adjudication.thermal_image_id == img_id,
                                                        Adjudication.is_current.is_(True)))
        doc = {"schema": "irtcri.annotations/1.0", "thermal_image_id": img_id,
               "readings": [{"reader_role": a.reader_role, "version": a.version, "label": a.label,
                             "confidence": a.confidence, "blinding_profile": a.blinding_profile,
                             "insertion_point": a.insertion_point, "roi": a.roi, "reference_roi": a.reference_roi,
                             "submitted_time": a.submitted_time.isoformat() if a.submitted_time else None}
                            for a in anns],
               "reference_label": None if adj is None else {"final_label": adj.final_label, "method": adj.method,
                                                            "criteria_version": adj.criteria_version}}
        data = json.dumps(doc, indent=2).encode()
        ref = image_ref(session, row, "thermal")
        loc = service.store_derived(ref, "annotation", f"{scan_id('thermal', ref.sha256)}_annotations.json", data,
                                    local=False, content_type="application/json")
        if loc["xnat"]:
            record_external(session, "xnat", "thermal_image", img_id, "annotation", loc["xnat"], sha256_bytes(data))
            n += 1
    return n
