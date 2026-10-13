from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.security import Perm, Principal
from irtcri.db.models import CatheterEpisode, Patient, ScreeningLog
from irtcri.hub import schemas as S
from irtcri.hub import services as svc
from irtcri.hub.deps import get_db, require

router = APIRouter(tags=["enrolment"])


@router.post("/screening", status_code=201)
def screening(body: S.ScreeningIn, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.ENROL))):
    return svc.as_dict(svc.create_screening(db, who, body))


@router.get("/screening")
def list_screening(db: Session = Depends(get_db), who: Principal = Depends(require(Perm.READ_CLINICAL))):
    return [svc.as_dict(r) for r in db.scalars(select(ScreeningLog)) if who.can_access_site(r.site_id)]


@router.post("/patients", status_code=201)
def enrol(body: S.PatientIn, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.ENROL))):
    p = svc.create_patient(db, who, body)
    return {"id": p.id, "study_id": p.study_id, "dq_flags": p.dq_flags}


@router.get("/patients")
def list_patients(site_code: str | None = None, db: Session = Depends(get_db),
                  who: Principal = Depends(require(Perm.READ_CLINICAL))):
    q = select(Patient).where(Patient.is_voided.is_(False)).order_by(Patient.study_id)
    if site_code:
        q = q.where(Patient.site_id == svc.get_site(db, site_code).id)
    return [{"id": p.id, "study_id": p.study_id, "site_id": p.site_id, "enrolment_date": p.enrolment_date,
             "sex": p.sex, "age_years": p.age_years, "withdrawn": p.withdrawn_time is not None}
            for p in db.scalars(q) if who.can_access_site(p.site_id)]


@router.get("/patients/{study_id}")
def get_patient(study_id: str, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.READ_CLINICAL))):
    p = svc.get_patient(db, who, study_id)
    eps = db.scalars(select(CatheterEpisode).where(CatheterEpisode.patient_id == p.id,
                                                    CatheterEpisode.is_voided.is_(False))).all()
    return {**svc.as_dict(p), "episodes": [svc.as_dict(e) for e in eps]}


@router.post("/patients/{study_id}/withdrawal", summary="Record consent withdrawal (no data are erased)")
def withdraw(study_id: str, body: S.WithdrawalIn, db: Session = Depends(get_db),
             who: Principal = Depends(require(Perm.CORRECT_DATA))):
    p = svc.withdraw_patient(db, who, study_id, body)
    return {"study_id": p.study_id, "withdrawn_time": p.withdrawn_time,
            "data_use_after_withdrawal": p.data_use_after_withdrawal}


@router.post("/encounters", status_code=201)
def encounter(body: S.EncounterIn, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.ENROL))):
    return svc.as_dict(svc.create_encounter(db, who, body))


@router.post("/episodes", status_code=201)
def episode(body: S.EpisodeIn, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.ENROL))):
    e = svc.create_episode(db, who, body)
    return {"id": e.id, "episode_code": e.episode_code}


@router.get("/episodes")
def list_episodes(study_id: str | None = None, db: Session = Depends(get_db),
                  who: Principal = Depends(require(Perm.READ_CLINICAL))):
    q = select(CatheterEpisode).where(CatheterEpisode.is_voided.is_(False))
    if study_id:
        q = q.where(CatheterEpisode.patient_id == svc.get_patient(db, who, study_id).id)
    return [svc.as_dict(e) for e in db.scalars(q) if who.can_access_site(e.site_id)]
