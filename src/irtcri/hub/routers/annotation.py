from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.annotation import workflow as wf
from irtcri.annotation.agreement import agreement_report
from irtcri.annotation.blinding import build_task_view, load_profiles, task_code
from irtcri.core.security import Perm, Principal
from irtcri.db.audit import audit
from irtcri.db.models import AnnotationAssignment, ThermalImage
from irtcri.hub import schemas as S
from irtcri.hub import services as svc
from irtcri.hub.deps import get_db, require

router = APIRouter(prefix="/annotation", tags=["annotation"])


@router.get("/profiles")
def profiles(_: Principal = Depends(require(Perm.ANNOTATE))):
    return {k: v.model_dump() for k, v in load_profiles().items()}


@router.post("/assignments", status_code=201)
def assign(body: S.AssignmentIn, db: Session = Depends(get_db),
           who: Principal = Depends(require(Perm.MANAGE_ANNOTATION))):
    return [svc.as_dict(a) for a in wf.assign(db, who, body)]


@router.get("/worklist")
def worklist(db: Session = Depends(get_db), who: Principal = Depends(require(Perm.ANNOTATE))):
    out = []
    for a in db.scalars(select(AnnotationAssignment).where(AnnotationAssignment.annotator_id == who.user_id)
                        .order_by(AnnotationAssignment.created_time)):
        cur = wf.current_annotation(db, a.thermal_image_id, who.user_id)
        out.append({"thermal_image_id": a.thermal_image_id, "task_code": task_code(a.thermal_image_id, who.user_id),
                    "reader_role": a.reader_role, "blinding_profile": a.blinding_profile,
                    "status": cur.status if cur else "todo"})
    return out


@router.get("/tasks/{image_id}")
def task(image_id: str, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.ANNOTATE))):
    a = wf.get_assignment(db, who, image_id)
    view = build_task_view(db, db.get(ThermalImage, image_id), who.user_id, a.blinding_profile)
    cur = wf.current_annotation(db, image_id, who.user_id)
    view["my_annotation"] = svc.as_dict(cur) if cur else None
    view["reader_role"] = a.reader_role
    audit(db, who, "read", "annotation_task", image_id, reason=f"profile={a.blinding_profile}")
    return view


@router.put("/tasks/{image_id}")
def save(image_id: str, body: S.AnnotationIn, db: Session = Depends(get_db),
         who: Principal = Depends(require(Perm.ANNOTATE))):
    return svc.as_dict(wf.save_annotation(db, who, image_id, body))


@router.get("/adjudication-queue")
def queue(db: Session = Depends(get_db), _: Principal = Depends(require(Perm.ADJUDICATE))):
    return wf.adjudication_queue(db)


@router.post("/adjudications", status_code=201)
def adjudicate(body: S.AdjudicationIn, db: Session = Depends(get_db),
               who: Principal = Depends(require(Perm.ADJUDICATE))):
    return svc.as_dict(wf.adjudicate(db, who, body))


@router.get("/agreement")
def agreement(n_boot: int = 500, db: Session = Depends(get_db),
              _: Principal = Depends(require(Perm.EXPORT_DATA))):
    return agreement_report(wf.reader_pairs(db), n_boot=n_boot)


@router.get("/agreement.csv", response_class=PlainTextResponse)
def agreement_csv(db: Session = Depends(get_db), who: Principal = Depends(require(Perm.EXPORT_DATA))):
    audit(db, who, "export", "annotation_pairs", None)
    return wf.reader_pairs(db).to_csv(index=False)
