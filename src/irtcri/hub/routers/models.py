from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.config import get_settings
from irtcri.core.provenance import code_version
from irtcri.core.security import Perm, Principal, Role
from irtcri.db.audit import audit, row_snapshot
from irtcri.db.models import ModelPrediction, ModelVersion
from irtcri.hub import schemas as S
from irtcri.hub import services as svc
from irtcri.hub.deps import get_db, require
from irtcri.ml.governance import GovernanceError, alert_payload, check_prediction_mode

router = APIRouter(tags=["models & predictions"])


@router.post("/models", status_code=201, summary="Register a model version (model card)")
def register(body: S.ModelVersionIn, db: Session = Depends(get_db),
             who: Principal = Depends(require(Perm.MANAGE_MODELS))):
    if db.scalar(select(ModelVersion).where(ModelVersion.name == body.name, ModelVersion.version == body.version)):
        raise HTTPException(409, "model version exists (versions are immutable)")
    mv = ModelVersion(**body.model_dump(), code_version=code_version()["git_commit"])
    db.add(mv)
    db.flush()
    audit(db, who, "create", "model_version", mv.id, after=row_snapshot(mv))
    return svc.as_dict(mv)


@router.get("/models")
def list_models(db: Session = Depends(get_db), _: Principal = Depends(require(Perm.VIEW_PREDICTIONS))):
    return [svc.as_dict(m) for m in db.scalars(select(ModelVersion).order_by(ModelVersion.created_time))]


@router.post("/models/{model_id}/status")
def set_status(model_id: str, body: S.ModelStatusIn, db: Session = Depends(get_db),
               who: Principal = Depends(require(Perm.MANAGE_MODELS))):
    mv = db.get(ModelVersion, model_id)
    if mv is None:
        raise HTTPException(404, "unknown model")
    if body.status == "validated_locked":
        if who.role != Role.ADMIN:
            raise HTTPException(403, "only Admin can lock a validated model")
        if not body.approval_reference:
            raise HTTPException(422, "approval_reference (validation report / ethics amendment) required")
    before = {"status": mv.status, "approval_reference": mv.approval_reference}
    mv.status, mv.approval_reference = body.status, body.approval_reference or mv.approval_reference
    audit(db, who, "status_change", "model_version", mv.id, before=before,
          after={"status": mv.status, "approval_reference": mv.approval_reference}, reason=body.reason)
    return svc.as_dict(mv)


@router.post("/predictions", status_code=201)
def store_prediction(body: S.PredictionIn, db: Session = Depends(get_db),
                     who: Principal = Depends(require(Perm.WRITE_PREDICTIONS))):
    mv = db.scalar(select(ModelVersion).where(ModelVersion.name == body.model_name,
                                              ModelVersion.version == body.model_version))
    if mv is None:
        raise HTTPException(404, "unknown model version")
    try:
        check_prediction_mode(body.mode, mv.status, get_settings())
    except GovernanceError as e:
        raise HTTPException(403, str(e)) from e
    ep = svc.get_episode(db, who, body.episode_code)
    p = ModelPrediction(model_version_id=mv.id, patient_id=ep.patient_id, episode_id=ep.id,
                        thermal_image_id=body.thermal_image_id, rgb_image_id=body.rgb_image_id,
                        prediction_time=body.prediction_time, mode=body.mode, probability=body.probability,
                        threshold=mv.threshold, predicted_positive=body.probability >= mv.threshold,
                        feature_contributions=body.feature_contributions,
                        explanation_object_key=body.explanation_object_key, input_provenance=body.input_provenance)
    db.add(p)
    db.flush()
    audit(db, who, "create", "model_prediction", p.id, after=row_snapshot(p))
    return {**svc.as_dict(p), "display": alert_payload(p.probability, p.threshold, f"{mv.name} v{mv.version}")}


@router.get("/analyses", summary="Manuscript analysis registry (read-only; records are created via CLI)")
def analyses(db: Session = Depends(get_db), _: Principal = Depends(require(Perm.EXPORT_DATA))):
    from irtcri.db.models import AnalysisRecord
    return [svc.as_dict(r) for r in db.scalars(select(AnalysisRecord).order_by(AnalysisRecord.date_created))]
