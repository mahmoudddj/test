"""Pilot research dashboard (timeline) and data-quality monitoring endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri import INVESTIGATIONAL_DISCLAIMER
from irtcri.core.config import get_settings
from irtcri.core.security import Perm, Principal
from irtcri.db.audit import audit
from irtcri.db.models import (
    Adjudication,
    CatheterEpisode,
    ClinicalAssessment,
    ClinicalObservation,
    ImagePair,
    LaboratoryResult,
    MedicationExposure,
    MicrobiologyResult,
    ModelPrediction,
    ModelVersion,
    Outcome,
    ThermalImage,
)
from irtcri.hub import services as svc
from irtcri.hub.deps import get_db, require
from irtcri.ml.governance import alert_payload
from irtcri.monitoring.dq import compute_dq, patient_drilldown
from irtcri.monitoring.site_report import site_report_html, site_report_markdown
from irtcri.pipeline.snapshot import load_tables

router = APIRouter(tags=["dashboards"])


def _site_filter(who: Principal) -> list[str] | None:
    return None if who.can(Perm.ALL_SITES) else [who.site_id]


@router.get("/patients/{study_id}/timeline")
def timeline(study_id: str, include_predictions: bool = True, db: Session = Depends(get_db),
             who: Principal = Depends(require(Perm.READ_CLINICAL))):
    p = svc.get_patient(db, who, study_id)
    audit(db, who, "read", "patient_timeline", p.id)
    eps = db.scalars(select(CatheterEpisode).where(CatheterEpisode.patient_id == p.id,
                                                    CatheterEpisode.is_voided.is_(False))).all()

    def rows(Model, *conds):
        return db.scalars(select(Model).where(Model.patient_id == p.id, Model.is_voided.is_(False), *conds)).all()

    out = {"study_id": p.study_id, "disclaimer": INVESTIGATIONAL_DISCLAIMER, "episodes": []}
    for e in eps:
        imgs = db.scalars(select(ThermalImage).where(ThermalImage.episode_id == e.id,
                                                     ThermalImage.is_voided.is_(False))
                          .order_by(ThermalImage.acquisition_time)).all()
        img_ids = [i.id for i in imgs]
        pairs = {pp.thermal_image_id: pp.rgb_image_id for pp in db.scalars(
            select(ImagePair).where(ImagePair.thermal_image_id.in_(img_ids)))} if img_ids else {}
        adjs = {a.thermal_image_id: a.final_label for a in db.scalars(select(Adjudication).where(
            Adjudication.thermal_image_id.in_(img_ids), Adjudication.is_current.is_(True)))} if img_ids else {}
        oc = db.scalar(select(Outcome).where(Outcome.episode_id == e.id, Outcome.is_voided.is_(False)))
        ep = {
            **{k: v for k, v in svc.as_dict(e).items() if k in (
                "id", "episode_code", "catheter_type", "insertion_site", "tunnelled", "insertion_time",
                "removal_time", "removal_reason", "follow_up_start_time", "end_of_follow_up_time",
                "imaging_interval_hours")},
            "images": [{"id": i.id, "time": i.acquisition_time.isoformat(), "view": i.view,
                        "qc_status": i.qc_override_status or i.qc_status, "t_max": i.t_max,
                        "rgb_image_id": pairs.get(i.id), "reference_label": adjs.get(i.id),
                        "acquisition_id": i.acquisition_id, "in_xnat": i.xnat_uri is not None} for i in imgs],
            "assessments": [{"time": a.event_time.isoformat(), "impression": a.clinical_impression,
                             "erythema_mm": a.erythema_mm, "purulent_discharge": a.purulent_discharge,
                             "tenderness": a.tenderness}
                            for a in rows(ClinicalAssessment, ClinicalAssessment.episode_id == e.id)],
            "outcome": None if oc is None else {
                "outcome_type": oc.outcome_type, "is_event": oc.is_event, "status": oc.status,
                "clinical_recognition_time": svc.iso(oc.clinical_recognition_time),
                "onset_time_reference": svc.iso(oc.onset_time_reference),
                "treatment_start_time": svc.iso(oc.treatment_start_time)},
        }
        if include_predictions and who.can(Perm.VIEW_PREDICTIONS):
            preds = db.execute(select(ModelPrediction, ModelVersion).join(
                ModelVersion, ModelVersion.id == ModelPrediction.model_version_id).where(
                ModelPrediction.episode_id == e.id).order_by(ModelPrediction.prediction_time)).all()
            ep["predictions"] = [{
                "id": pr.id, "time": pr.prediction_time.isoformat(), "mode": pr.mode,
                "thermal_image_id": pr.thermal_image_id, "model_version": f"{mv.name} v{mv.version}",
                "model_status": mv.status, "has_explanation": pr.explanation_object_key is not None,
                "feature_contributions": pr.feature_contributions,
                **alert_payload(pr.probability, pr.threshold, f"{mv.name} v{mv.version}")} for pr, mv in preds]
        out["episodes"].append(ep)
    out["observations"] = [{"time": o.event_time.isoformat(), "variable": o.variable, "value": o.value_num,
                            "unit": o.unit} for o in rows(ClinicalObservation)]
    out["labs"] = [{"time": o.event_time.isoformat(), "reported": svc.iso(o.source_time), "variable": o.variable,
                    "value": o.value_num, "unit": o.unit} for o in rows(LaboratoryResult)]
    out["antibiotics"] = [{"drug": m.drug_name, "start": m.start_time.isoformat(), "end": svc.iso(m.end_time),
                           "route": m.route, "indication": m.indication}
                          for m in rows(MedicationExposure, MedicationExposure.is_antibiotic.is_(True))]
    out["microbiology"] = [{"time": m.event_time.isoformat(), "reported": svc.iso(m.source_time),
                            "specimen": m.specimen_type, "growth": m.growth, "organism": m.organism}
                           for m in rows(MicrobiologyResult)]
    out["prospective_alerts_enabled"] = get_settings().prospective_alerts_enabled
    return out


@router.get("/dq/summary")
def dq_summary(db: Session = Depends(get_db), who: Principal = Depends(require(Perm.VIEW_DQ))):
    return compute_dq(load_tables(db, site_ids=_site_filter(who)))


@router.get("/dq/patients/{study_id}")
def dq_patient(study_id: str, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.VIEW_DQ))):
    p = svc.get_patient(db, who, study_id)
    audit(db, who, "read", "dq_drilldown", p.id)
    res = patient_drilldown(load_tables(db, site_ids=_site_filter(who)), study_id)
    if not res:
        raise HTTPException(404, "not found")
    return res


@router.get("/dq/site-report/{site_code}")
def site_report(site_code: str, month: str, fmt: str = "html", db: Session = Depends(get_db),
                who: Principal = Depends(require(Perm.VIEW_DQ))):
    site = svc.get_site(db, site_code)
    if not who.can_access_site(site.id):
        raise HTTPException(403, "other site")
    t = load_tables(db, site_ids=[site.id])
    if fmt == "md":
        return PlainTextResponse(site_report_markdown(t, site_code, month))
    return HTMLResponse(site_report_html(t, site_code, month))
