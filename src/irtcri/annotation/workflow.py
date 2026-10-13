"""Annotation workflow: assignments, versioned blinded annotations, adjudication."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.annotation.blinding import load_profiles
from irtcri.core.security import Perm, Principal
from irtcri.core.timeutils import utcnow
from irtcri.db.audit import audit, row_snapshot
from irtcri.db.models import Adjudication, Annotation, AnnotationAssignment, ImagePair, ThermalImage, User
from irtcri.hub import schemas as S
from irtcri.hub.services import Conflict, Forbidden, NotFound, ValidationFailed


def assign(session: Session, who: Principal, body: S.AssignmentIn) -> list[AnnotationAssignment]:
    if not who.can(Perm.MANAGE_ANNOTATION):
        raise Forbidden("cannot manage annotation assignments")
    if body.blinding_profile not in load_profiles():
        raise ValidationFailed([f"unknown blinding profile '{body.blinding_profile}'"])
    user = session.scalar(select(User).where(User.username == body.annotator_username))
    if user is None:
        raise NotFound("unknown annotator")
    from irtcri.core.security import has_perm
    if not has_perm(user.role, Perm.ANNOTATE):
        raise ValidationFailed([f"user {user.username} does not have the annotate permission"])
    out = []
    for img_id in body.thermal_image_ids:
        if session.get(ThermalImage, img_id) is None:
            raise NotFound(f"thermal image {img_id}")
        existing = session.scalars(select(AnnotationAssignment).where(
            AnnotationAssignment.thermal_image_id == img_id)).all()
        if any(a.reader_role == body.reader_role for a in existing):
            raise Conflict(f"image {img_id} already has a {body.reader_role}")
        if any(a.annotator_id == user.id for a in existing):
            raise Conflict(f"{user.username} is already a reader for image {img_id} (readers must be independent)")
        a = AnnotationAssignment(thermal_image_id=img_id, annotator_id=user.id, reader_role=body.reader_role,
                                 blinding_profile=body.blinding_profile)
        session.add(a)
        out.append(a)
    session.flush()
    audit(session, who, "assign", "annotation_assignment", None,
          after={"images": body.thermal_image_ids, "annotator": user.username, "role": body.reader_role})
    return out


def get_assignment(session: Session, who: Principal, image_id: str) -> AnnotationAssignment:
    a = session.scalar(select(AnnotationAssignment).where(AnnotationAssignment.thermal_image_id == image_id,
                                                          AnnotationAssignment.annotator_id == who.user_id))
    if a is None:
        raise Forbidden("image not assigned to you")
    return a


def current_annotation(session: Session, image_id: str, annotator_id: str) -> Annotation | None:
    return session.scalar(select(Annotation).where(
        Annotation.thermal_image_id == image_id, Annotation.annotator_id == annotator_id,
        Annotation.status != "superseded").order_by(Annotation.version.desc()))


def save_annotation(session: Session, who: Principal, image_id: str, body: S.AnnotationIn) -> Annotation:
    a = get_assignment(session, who, image_id)
    cur = current_annotation(session, image_id, who.user_id)
    fields = body.model_dump(exclude={"submit"})
    if fields.get("label") is not None:
        fields["label"] = str(fields["label"].value if hasattr(fields["label"], "value") else fields["label"])
    if cur is not None and cur.status == "draft":
        for k, v in fields.items():
            setattr(cur, k, v)
        ann = cur
    else:
        pair = session.scalar(select(ImagePair).where(ImagePair.thermal_image_id == image_id))
        ann = Annotation(thermal_image_id=image_id, rgb_image_id=pair.rgb_image_id if pair else None,
                         annotator_id=who.user_id, reader_role=a.reader_role, blinding_profile=a.blinding_profile,
                         version=(cur.version + 1) if cur else 1, supersedes_id=cur.id if cur else None, **fields)
        if cur is not None:   # revising a submitted annotation keeps the old version
            cur.status = "superseded"
        session.add(ann)
    if body.submit:
        if ann.label is None:
            raise ValidationFailed(["a label is required to submit"])
        ann.status, ann.submitted_time = "submitted", utcnow()
    session.flush()
    audit(session, who, "submit" if body.submit else "save_draft", "annotation", ann.id, after=row_snapshot(ann))
    if body.submit:
        _auto_agreement(session, who, image_id)
    return ann


def _auto_agreement(session: Session, who: Principal, image_id: str) -> None:
    subs = submitted_by_role(session, image_id)
    if "reader_1" in subs and "reader_2" in subs:
        r1, r2 = subs["reader_1"], subs["reader_2"]
        _retire(session, image_id)
        if r1.label == r2.label:
            adj = Adjudication(level="image", thermal_image_id=image_id, reader1_annotation_id=r1.id,
                               reader2_annotation_id=r2.id, method="agreement", final_label=r1.label,
                               rationale="independent readers agreed")
            session.add(adj)
            session.flush()
            audit(session, who, "auto_adjudicate", "adjudication", adj.id, after=row_snapshot(adj))


def _retire(session: Session, image_id: str) -> None:
    for old in session.scalars(select(Adjudication).where(Adjudication.thermal_image_id == image_id,
                                                          Adjudication.is_current.is_(True))):
        old.is_current = False


def submitted_by_role(session: Session, image_id: str) -> dict[str, Annotation]:
    out = {}
    for a in session.scalars(select(Annotation).where(Annotation.thermal_image_id == image_id,
                                                      Annotation.status == "submitted")):
        out[a.reader_role] = a
    return out


def adjudication_queue(session: Session) -> list[dict]:
    images = session.scalars(select(Annotation.thermal_image_id).where(Annotation.status == "submitted")
                             .distinct()).all()
    out = []
    for img in images:
        subs = submitted_by_role(session, img)
        if not {"reader_1", "reader_2"} <= subs.keys():
            continue
        cur = session.scalar(select(Adjudication).where(Adjudication.thermal_image_id == img,
                                                        Adjudication.is_current.is_(True)))
        if cur is None:
            out.append({"thermal_image_id": img, "reader_1_label": subs["reader_1"].label,
                        "reader_2_label": subs["reader_2"].label})
    return out


def adjudicate(session: Session, who: Principal, body: S.AdjudicationIn) -> Adjudication:
    if not who.can(Perm.ADJUDICATE):
        raise Forbidden("adjudicator role required")
    subs = submitted_by_role(session, body.thermal_image_id)
    if not {"reader_1", "reader_2"} <= subs.keys():
        raise Conflict("both independent readings must be submitted before adjudication")
    if who.user_id in (subs["reader_1"].annotator_id, subs["reader_2"].annotator_id):
        raise Forbidden("an adjudicator cannot adjudicate an image they read")
    _retire(session, body.thermal_image_id)
    adj = Adjudication(level="image", thermal_image_id=body.thermal_image_id,
                       reader1_annotation_id=subs["reader_1"].id, reader2_annotation_id=subs["reader_2"].id,
                       adjudicator_id=who.user_id, method="adjudicator", final_label=body.final_label.value,
                       rationale=body.rationale, criteria_version=body.criteria_version)
    session.add(adj)
    session.flush()
    audit(session, who, "adjudicate", "adjudication", adj.id, after=row_snapshot(adj))
    return adj


def reader_pairs(session: Session) -> pd.DataFrame:
    rows = []
    imgs = session.scalars(select(Annotation.thermal_image_id).where(Annotation.status == "submitted")
                           .distinct()).all()
    for img in imgs:
        subs = submitted_by_role(session, img)
        if {"reader_1", "reader_2"} <= subs.keys():
            th = session.get(ThermalImage, img)
            adj = session.scalar(select(Adjudication).where(Adjudication.thermal_image_id == img,
                                                            Adjudication.is_current.is_(True)))
            rows.append({"thermal_image_id": img, "patient_id": th.patient_id, "site_id": th.site_id,
                         "label_r1": subs["reader_1"].label, "label_r2": subs["reader_2"].label,
                         "confidence_r1": subs["reader_1"].confidence, "confidence_r2": subs["reader_2"].confidence,
                         "blinding_r1": subs["reader_1"].blinding_profile,
                         "blinding_r2": subs["reader_2"].blinding_profile,
                         "final_label": adj.final_label if adj else None,
                         "final_method": adj.method if adj else None})
    return pd.DataFrame(rows, columns=["thermal_image_id", "patient_id", "site_id", "label_r1", "label_r2",
                                       "confidence_r1", "confidence_r2", "blinding_r1", "blinding_r2",
                                       "final_label", "final_method"])
