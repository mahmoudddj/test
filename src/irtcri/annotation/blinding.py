"""Blinded annotation task views.

The server - not the browser - decides what a reader may see, so blinding
cannot be bypassed by inspecting network traffic.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.config import get_settings
from irtcri.db.models import (
    Annotation,
    CatheterEpisode,
    ClinicalAssessment,
    ClinicalObservation,
    ImagePair,
    LaboratoryResult,
    MedicationExposure,
    MicrobiologyResult,
    Outcome,
    Patient,
    ThermalImage,
)


class BlindingProfile(BaseModel):
    description: str = ""
    show_rgb: bool = True
    mask_identity: bool = True
    show_acquisition_time: bool = False
    show_catheter_info: bool = True
    show_clinical_before_image_hours: float = 0
    show_assessments: bool = False
    show_microbiology: bool = False
    show_antibiotics: bool = False
    show_outcome: bool = False
    show_other_annotations: bool = False
    show_model_predictions: bool = False


@lru_cache
def load_profiles(path: str | None = None) -> dict[str, BlindingProfile]:
    p = Path(path) if path else get_settings().config_path("annotation/blinding.yaml")
    raw = yaml.safe_load(open(p))
    return {k: BlindingProfile(**v) for k, v in raw["profiles"].items()}


def task_code(image_id: str, annotator_id: str) -> str:
    """Opaque, reader-specific code so readers cannot link images across time/patients."""
    return "T-" + hashlib.sha256(f"{image_id}|{annotator_id}".encode()).hexdigest()[:10].upper()


def build_task_view(session: Session, image: ThermalImage, annotator_id: str, profile_name: str) -> dict[str, Any]:
    prof = load_profiles()[profile_name]
    ep = session.get(CatheterEpisode, image.episode_id)
    patient = session.get(Patient, image.patient_id)
    t = image.acquisition_time
    view: dict[str, Any] = {
        "task_code": task_code(image.id, annotator_id),
        "thermal_image_id": image.id,
        "blinding_profile": profile_name,
        "view": image.view,
        "is_radiometric": image.is_radiometric,
        "width": image.width, "height": image.height,
    }
    if not prof.mask_identity:
        view.update(study_id=patient.study_id, episode_code=ep.episode_code)
    if prof.show_acquisition_time:
        view["acquisition_time"] = t.isoformat()
        view["hours_since_insertion"] = round((t - ep.insertion_time).total_seconds() / 3600, 1)
    if prof.show_catheter_info:
        view.update(catheter_type=ep.catheter_type, insertion_site=ep.insertion_site, tunnelled=ep.tunnelled)
    if prof.show_rgb:
        pair = session.scalar(select(ImagePair).where(ImagePair.thermal_image_id == image.id))
        view["rgb_image_id"] = pair.rgb_image_id if pair else None
        view["registration_matrix"] = pair.registration_matrix if pair else None
    if prof.show_clinical_before_image_hours > 0:
        lo = t - timedelta(hours=prof.show_clinical_before_image_hours)
        rows = []
        for M in (ClinicalObservation, LaboratoryResult):
            q = select(M).where(M.patient_id == patient.id, M.is_voided.is_(False), M.event_time >= lo,
                                M.event_time <= t)
            for r in session.scalars(q):
                # only results already reported at image time (no peeking at later lab reports)
                if r.source_time is not None and r.source_time > t:
                    continue
                rows.append({"variable": r.variable, "value": r.value_num, "unit": r.unit,
                             "hours_before_image": round((t - r.event_time).total_seconds() / 3600, 1)})
        view["clinical_context"] = sorted(rows, key=lambda x: (x["variable"], x["hours_before_image"]))
    if prof.show_assessments:
        view["assessments"] = [
            {"time": a.event_time.isoformat(), "erythema_mm": a.erythema_mm, "induration_mm": a.induration_mm,
             "tenderness": a.tenderness, "purulent_discharge": a.purulent_discharge,
             "clinical_impression": a.clinical_impression}
            for a in session.scalars(select(ClinicalAssessment).where(
                ClinicalAssessment.episode_id == ep.id, ClinicalAssessment.is_voided.is_(False)))]
    if prof.show_microbiology:
        view["microbiology"] = [
            {"time": m.event_time.isoformat(), "specimen": m.specimen_type, "growth": m.growth,
             "organism": m.organism}
            for m in session.scalars(select(MicrobiologyResult).where(
                MicrobiologyResult.patient_id == patient.id, MicrobiologyResult.is_voided.is_(False)))]
    if prof.show_antibiotics:
        view["antibiotics"] = [
            {"drug": m.drug_name, "start": m.start_time.isoformat(), "end": m.end_time.isoformat() if m.end_time
             else None}
            for m in session.scalars(select(MedicationExposure).where(
                MedicationExposure.patient_id == patient.id, MedicationExposure.is_antibiotic.is_(True),
                MedicationExposure.is_voided.is_(False)))]
    if prof.show_outcome:
        o = session.scalar(select(Outcome).where(Outcome.episode_id == ep.id, Outcome.is_voided.is_(False)))
        view["outcome"] = None if o is None else {
            "outcome_type": o.outcome_type, "status": o.status,
            "clinical_recognition_time": o.clinical_recognition_time.isoformat() if o.clinical_recognition_time
            else None}
    if prof.show_other_annotations:
        view["other_annotations"] = [
            {"reader_role": a.reader_role, "label": a.label, "confidence": a.confidence, "comment": a.comment,
             "roi": a.roi, "insertion_point": a.insertion_point}
            for a in session.scalars(select(Annotation).where(
                Annotation.thermal_image_id == image.id, Annotation.status == "submitted",
                Annotation.annotator_id != annotator_id))]
    return view
