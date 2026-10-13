from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.security import Perm, Principal
from irtcri.core.timeutils import utcnow
from irtcri.db.audit import audit
from irtcri.db.models import DataQuery
from irtcri.hub import schemas as S
from irtcri.hub import services as svc
from irtcri.hub.deps import get_db, require

router = APIRouter(tags=["clinical data"])


@router.post("/measurements", status_code=201, summary="Clinical observation or laboratory result")
def measurement(body: S.MeasurementIn, db: Session = Depends(get_db),
                who: Principal = Depends(require(Perm.WRITE_CLINICAL))):
    r = svc.create_measurement(db, who, body)
    return {"id": r.id, "table": r.__tablename__, "variable": r.variable, "value": r.value_num, "unit": r.unit,
            "dq_flags": r.dq_flags}


@router.post("/medications", status_code=201)
def medication(body: S.MedicationIn, db: Session = Depends(get_db),
               who: Principal = Depends(require(Perm.WRITE_CLINICAL))):
    return svc.as_dict(svc.create_medication(db, who, body))


@router.post("/microbiology", status_code=201)
def microbiology(body: S.MicrobiologyIn, db: Session = Depends(get_db),
                 who: Principal = Depends(require(Perm.WRITE_CLINICAL))):
    return svc.as_dict(svc.create_microbiology(db, who, body))


@router.post("/assessments", status_code=201)
def assessment(body: S.AssessmentIn, db: Session = Depends(get_db),
               who: Principal = Depends(require(Perm.WRITE_CLINICAL))):
    return svc.as_dict(svc.create_assessment(db, who, body))


@router.post("/outcomes", status_code=201)
def outcome(body: S.OutcomeIn, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.WRITE_OUTCOME))):
    return svc.as_dict(svc.create_outcome(db, who, body))


@router.post("/deviations", status_code=201)
def deviation(body: S.DeviationIn, db: Session = Depends(get_db),
              who: Principal = Depends(require(Perm.WRITE_CLINICAL))):
    return svc.as_dict(svc.create_deviation(db, who, body))


@router.patch("/records/{entity_type}/{entity_id}", summary="Correct a record (reason required, fully audited)")
def correct(entity_type: str, entity_id: str, body: S.CorrectionIn, db: Session = Depends(get_db),
            who: Principal = Depends(require(Perm.CORRECT_DATA))):
    row = svc.correct_record(db, who, entity_type, entity_id, body.changes, body.reason, body.expected_version)
    return svc.as_dict(row)


@router.post("/records/{entity_type}/{entity_id}/void", summary="Void a record (soft delete with reason)")
def void(entity_type: str, entity_id: str, body: S.VoidIn, db: Session = Depends(get_db),
         who: Principal = Depends(require(Perm.CORRECT_DATA))):
    row = svc.void_record(db, who, entity_type, entity_id, body.reason)
    return {"id": row.id, "is_voided": row.is_voided}


# ------------------------------------------------------------------ queries
@router.post("/queries", status_code=201)
def raise_query(body: S.QueryIn, db: Session = Depends(get_db), who: Principal = Depends(require(Perm.RAISE_QUERY))):
    Model = svc.CORRECTABLE.get(body.entity_type)
    row = db.get(Model, body.entity_id) if Model else None
    if row is None:
        raise HTTPException(404, "referenced record not found")
    site_id = svc._site_of(db, row)
    if not who.can_access_site(site_id):
        raise HTTPException(403, "other site")
    q = DataQuery(site_id=site_id, raised_by=who.user_id, **body.model_dump())
    db.add(q)
    db.flush()
    audit(db, who, "create", "data_query", q.id, after=body.model_dump())
    return svc.as_dict(q)


@router.get("/queries")
def list_queries(status: str | None = "open", db: Session = Depends(get_db),
                 who: Principal = Depends(require(Perm.VIEW_DQ))):
    q = select(DataQuery).order_by(DataQuery.raised_time)
    if status:
        q = q.where(DataQuery.status == status)
    return [svc.as_dict(x) for x in db.scalars(q) if who.can_access_site(x.site_id)]


@router.patch("/queries/{query_id}")
def respond_query(query_id: str, body: S.QueryResponseIn, db: Session = Depends(get_db),
                  who: Principal = Depends(require(Perm.RAISE_QUERY))):
    q = db.get(DataQuery, query_id)
    if q is None or not who.can_access_site(q.site_id):
        raise HTTPException(404, "query not found")
    if body.status == "closed" and not who.can(Perm.MANAGE_QUERIES):
        raise HTTPException(403, "only data managers close queries")
    before = svc.as_dict(q)
    q.status = body.status
    if body.response:
        q.response = body.response
    if body.status == "closed":
        q.closed_time = utcnow()
    audit(db, who, "update", "data_query", q.id, before=before, after=svc.as_dict(q))
    return svc.as_dict(q)
