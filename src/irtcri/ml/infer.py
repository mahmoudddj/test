"""Retrospective replay: compute predictions (+ Grad-CAM, clinical attributions) for dataset samples
and store them as ModelPrediction rows for the pilot dashboard."""

from __future__ import annotations

import io
import json

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.config import get_settings
from irtcri.core.security import Principal
from irtcri.db.audit import audit
from irtcri.db.models import ModelPrediction, ModelVersion
from irtcri.imaging.publish import publish_prediction
from irtcri.imaging.storage import derived_bucket, get_object_store
from irtcri.interpret.attributions import linear_contributions, occlusion_contributions, shap_contributions
from irtcri.ml.governance import check_prediction_mode
from irtcri.ml.train import LoadedModel
from irtcri.pipeline.builder import load_ml_dataset


def register_model(session: Session, who: Principal, model: LoadedModel) -> ModelVersion:
    c = model.card
    mv = session.scalar(select(ModelVersion).where(ModelVersion.name == c["model_name"],
                                                   ModelVersion.version == c["model_version"]))
    if mv:
        return mv
    mv = ModelVersion(name=c["model_name"], version=c["model_version"], family=c["family"],
                      training_dataset_version=f"{c['dataset']['name']}@{c['dataset']['version']}",
                      code_version=(c["code_version"] or {}).get("git_commit"), hyperparameters=c["hyperparameters"],
                      intended_use=c["intended_use"], limitations=c["limitations"],
                      metrics={"validation": c["validation_metrics"]}, threshold=c["threshold"],
                      threshold_rule=str(c["threshold_selection"]["rule"]), calibration=c["calibration"],
                      validation_population=str(c["validation_population"]), artifact_uri=str(model.run_dir))
    session.add(mv)
    session.flush()
    audit(session, who, "create", "model_version", mv.id, after={"name": mv.name, "version": mv.version})
    return mv


def explain_clinical(model: LoadedModel, X_std: np.ndarray, i: int) -> dict | None:
    if model.prep is None:
        return None
    names = model.prep.columns
    if model.net is None and hasattr(model.est, "coef_"):
        return linear_contributions(model.est.coef_, X_std[i], names)
    if model.net is None:
        predict = lambda Z: model.est.predict_proba(Z)[:, 1]  # noqa: E731
        res = shap_contributions(predict, X_std[i], X_std[: min(len(X_std), 100)], names)
        return res or occlusion_contributions(predict, X_std[i], np.zeros(len(names)), names)
    return None   # multimodal nets: clinical-branch attribution via occlusion is computed offline (costly)


def replay(session: Session, who: Principal, run_dir: str, splits: tuple[str, ...] = ("val",),
           mode: str = "retrospective_replay", gradcam: bool = True, limit: int | None = None) -> int:
    """Store predictions for the given splits. Test splits are allowed only after locked evaluation."""
    model = LoadedModel(run_dir)
    locked = {"test", "external_test"} & set(splits)
    if locked:
        from pathlib import Path
        ledger = Path(model.card["dataset"]["root"]) / "test_access_ledger.jsonl"
        evaluated = ledger.exists() and any(
            json.loads(x).get("run_dir") == str(run_dir) and json.loads(x).get("split") in locked
            for x in ledger.read_text().splitlines())
        if not evaluated:
            raise PermissionError("replay of locked splits is allowed only after the final locked evaluation "
                                  "of this model (irtcri evaluate-test --confirm-final)")
    mv = register_model(session, who, model)
    check_prediction_mode(mode, mv.status, get_settings())
    samples, _, _ = load_ml_dataset(model.card["dataset"]["root"])
    s = samples[samples["split"].isin(splits)].reset_index(drop=True)
    if limit:
        s = s.head(limit)
    probs = model.predict(s)
    X_std = model.prep.transform(s) if model.prep is not None else None
    n = 0
    for i, r in s.iterrows():
        contrib = explain_clinical(model, X_std, i) if X_std is not None else None
        key, cam = None, None
        if gradcam and model.net is not None and "thermal" in model.modalities:
            key, cam = _gradcam_to_store(model, s.iloc[[i]], mv.id, r["thermal_image_id"])
        pred = ModelPrediction(
            model_version_id=mv.id, patient_id=r["patient_id"], episode_id=r["episode_id"],
            thermal_image_id=r["thermal_image_id"], rgb_image_id=r["rgb_image_id"] if isinstance(
                r["rgb_image_id"], str) else None,
            prediction_time=pd.Timestamp(r["prediction_time"]).to_pydatetime(), mode=mode,
            probability=float(probs[i]), threshold=model.threshold,
            predicted_positive=bool(probs[i] >= model.threshold),
            explanation_object_key=key, feature_contributions=contrib,
            input_provenance={"dataset": model.card["dataset"], "split": r["split"]})
        session.add(pred)
        session.flush()
        publish_prediction(session, pred, mv, model.card, cam)   # no-op unless XNAT write-back is enabled
        n += 1
    audit(session, who, "replay", "model_prediction", mv.id, after={"n": n, "splits": list(splits), "mode": mode})
    session.commit()
    return n


def _gradcam_to_store(model: LoadedModel, row: pd.DataFrame, mv_id: str, image_id: str
                      ) -> tuple[str | None, np.ndarray | None]:
    import torch

    from irtcri.interpret.gradcam import grad_cam
    from irtcri.ml.train import _loader
    try:
        batch = next(iter(_loader(row.reset_index(drop=True), model.cfg, model.modalities, model.prep, False)))
        batch = {k: v for k, v in batch.items()}
        with torch.enable_grad():
            cam = grad_cam(model.net, batch, "thermal_enc")
    except NotImplementedError:
        return None, None
    buf = io.BytesIO()
    np.save(buf, cam.astype(np.float16), allow_pickle=False)
    key = f"gradcam/{mv_id}/{image_id}.npy"
    get_object_store().put(derived_bucket(), key, buf.getvalue())
    return key, cam
