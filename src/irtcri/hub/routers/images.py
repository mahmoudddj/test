from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.security import Perm, Principal
from irtcri.db.audit import audit
from irtcri.db.models import AnnotationAssignment, ImagePair, ModelPrediction, RGBImage, ThermalImage
from irtcri.hub import schemas as S
from irtcri.hub import services as svc
from irtcri.hub.deps import get_db, get_principal, require
from irtcri.imaging import ingest as ing
from irtcri.imaging.registration import estimate_affine
from irtcri.imaging.render import downsample_grid, palette_png, thermal_png
from irtcri.imaging.storage import derived_bucket, get_object_store

router = APIRouter(tags=["imaging"])
MODELS = {"thermal": ThermalImage, "rgb": RGBImage}


def _image_summary(r) -> dict:
    d = {k: v for k, v in svc.as_dict(r).items() if k not in ("raw_object_key",)}
    d["effective_qc_status"] = ing.effective_qc_status(r)
    return d


def _check_image_access(db: Session, who: Principal, modality: str, row) -> None:
    """READ_IMAGES users (site-scoped) or annotators assigned to this image (or its paired RGB)."""
    if who.can(Perm.READ_IMAGES) and who.can_access_site(row.site_id):
        return
    if who.can(Perm.ANNOTATE):
        th_id = row.id
        if modality == "rgb":
            pair = db.scalar(select(ImagePair).where(ImagePair.rgb_image_id == row.id))
            th_id = pair.thermal_image_id if pair else None
        if th_id and db.scalar(select(AnnotationAssignment).where(
                AnnotationAssignment.thermal_image_id == th_id, AnnotationAssignment.annotator_id == who.user_id)):
            return
    raise HTTPException(403, "not authorised for this image")


@router.post("/images/{modality}", status_code=201, summary="Upload a raw image with acquisition metadata")
async def upload(modality: str, file: UploadFile = File(...), metadata: str = Form(..., description="ImageMetaIn JSON"),
                 tiff_scale: float | None = Form(None), db: Session = Depends(get_db),
                 who: Principal = Depends(require(Perm.UPLOAD_IMAGES))):
    if modality not in MODELS:
        raise HTTPException(404, "modality must be thermal or rgb")
    try:
        meta_in = S.ImageMetaIn.model_validate(json.loads(metadata))
    except (ValidationError, json.JSONDecodeError) as e:
        raise HTTPException(422, f"invalid metadata: {e}") from e
    ep = svc.get_episode(db, who, meta_in.episode_code)
    meta = meta_in.model_dump(exclude={"episode_code"})
    meta["episode_id"] = ep.id
    meta["view"] = meta_in.view.value
    data = await file.read()
    opts = {"tiff_scale": tiff_scale} if (tiff_scale and modality == "thermal") else None
    res = ing.ingest_image(db, who, modality, data, file.filename or f"upload.{modality}", meta, "upload", opts)
    paired = None
    if res.pair:
        paired = res.pair.rgb_image_id if modality == "thermal" else res.pair.thermal_image_id
    return {"id": res.image.id, "qc_status": res.image.qc_status, "qc_reasons": res.image.qc_reasons,
            "already_present": res.already_present, "paired_with": paired}


@router.get("/images/{modality}")
def list_images(modality: str, episode_code: str | None = None, qc_status: str | None = None,
                limit: int = Query(500, le=5000), db: Session = Depends(get_db),
                who: Principal = Depends(require(Perm.READ_IMAGES))):
    M = MODELS[modality]
    q = select(M).where(M.is_voided.is_(False)).order_by(M.acquisition_time).limit(limit)
    if episode_code:
        q = q.where(M.episode_id == svc.get_episode(db, who, episode_code).id)
    if qc_status:
        q = q.where(M.qc_status == qc_status)
    return [_image_summary(r) for r in db.scalars(q) if who.can_access_site(r.site_id)]


@router.get("/images/{modality}/{image_id}")
def image_meta(modality: str, image_id: str, db: Session = Depends(get_db),
               who: Principal = Depends(require(Perm.READ_IMAGES))):
    r = db.get(MODELS[modality], image_id)
    if r is None or not who.can_access_site(r.site_id):
        raise HTTPException(404, "image not found")
    return _image_summary(r)


@router.get("/images/{modality}/{image_id}/render.png", response_class=Response)
def render(modality: str, image_id: str, prediction_id: str | None = None, wmin: float | None = None,
           wmax: float | None = None, db: Session = Depends(get_db), who: Principal = Depends(get_principal)):
    r = db.get(MODELS[modality], image_id)
    if r is None:
        raise HTTPException(404, "image not found")
    _check_image_access(db, who, modality, r)
    if modality == "rgb":
        return Response(palette_png(ing.load_rgb(r).pixels, 1024), media_type="image/png")
    dec = ing.load_temperature(r)
    if dec.temperature_c is None:
        return Response(palette_png(dec.pixels), media_type="image/png")
    overlay = None
    if prediction_id:
        if not who.can(Perm.VIEW_PREDICTIONS):
            raise HTTPException(403, "not authorised to view model outputs")
        pred = db.get(ModelPrediction, prediction_id)
        if pred and pred.explanation_object_key:
            import io

            import numpy as np

            from irtcri.imaging.derive import unletterbox
            cam = np.load(io.BytesIO(get_object_store().get(derived_bucket(), pred.explanation_object_key)))
            overlay = unletterbox(cam.astype(np.float32), dec.temperature_c.shape)
    window = (wmin, wmax) if wmin is not None and wmax is not None else None
    png, (lo, hi) = thermal_png(dec.temperature_c, window, overlay=overlay)
    return Response(png, media_type="image/png", headers={"X-Temperature-Window": f"{lo:.2f},{hi:.2f}"})


@router.get("/images/thermal/{image_id}/temperature")
def temperature_grid(image_id: str, max_side: int = Query(160, le=640), db: Session = Depends(get_db),
                     who: Principal = Depends(get_principal)):
    r = db.get(ThermalImage, image_id)
    if r is None:
        raise HTTPException(404, "image not found")
    _check_image_access(db, who, "thermal", r)
    dec = ing.load_temperature(r)
    if dec.temperature_c is None:
        raise HTTPException(409, "image is not radiometric")
    return {"unit": "degC", **downsample_grid(dec.temperature_c, max_side)}


@router.post("/images/{modality}/{image_id}/qc-override")
def qc_override(modality: str, image_id: str, body: S.QCOverrideIn, db: Session = Depends(get_db),
                who: Principal = Depends(require(Perm.CORRECT_DATA))):
    r = db.get(MODELS[modality], image_id)
    if r is None or not who.can_access_site(r.site_id):
        raise HTTPException(404, "image not found")
    before = {"qc_override_status": r.qc_override_status, "qc_override_reason": r.qc_override_reason}
    r.qc_override_status, r.qc_override_reason = body.status, body.reason
    audit(db, who, "qc_override", MODELS[modality].__tablename__, r.id, before=before,
          after={"qc_override_status": body.status, "qc_override_reason": body.reason}, reason=body.reason)
    return _image_summary(r)


@router.post("/images/{modality}/{image_id}/qc-rerun")
def qc_rerun(modality: str, image_id: str, db: Session = Depends(get_db),
             who: Principal = Depends(require(Perm.CORRECT_DATA))):
    r = db.get(MODELS[modality], image_id)
    if r is None or not who.can_access_site(r.site_id):
        raise HTTPException(404, "image not found")
    qc = ing.rerun_qc(db, who, modality, image_id)
    return {"qc_status": qc.status, "qc_reasons": qc.reasons, "qc_config_version": qc.config_version}


@router.post("/image-pairs/{pair_id}/registration")
def register_pair(pair_id: str, rgb_points: list[list[float]], thermal_points: list[list[float]],
                  db: Session = Depends(get_db), who: Principal = Depends(require(Perm.CORRECT_DATA))):
    pair = db.get(ImagePair, pair_id)
    if pair is None:
        raise HTTPException(404, "pair not found")
    M, rms = estimate_affine(rgb_points, thermal_points)
    pair.registration_method, pair.registration_matrix, pair.registration_error_px = (
        "affine_control_points", M.tolist(), rms)
    audit(db, who, "update", "image_pair", pair.id, after={"registration_matrix": M.tolist(), "rms_px": rms})
    return {"pair_id": pair.id, "matrix": M.tolist(), "rms_error_px": rms}
