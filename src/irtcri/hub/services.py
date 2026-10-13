"""Data Hub domain services.

Single implementation of every write path, used by the REST API, bulk
importers, the synthetic-data generator and the (optional) FHIR adapter.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, func, select
from sqlalchemy.orm import Session

from irtcri.core.security import Perm, Principal, Role, hash_token, new_token
from irtcri.core.timeutils import parse_timestamp
from irtcri.db.audit import audit, row_snapshot
from irtcri.db.base import Base, UTCDateTime
from irtcri.db.models import (
    CatheterEpisode,
    ClinicalAssessment,
    ClinicalObservation,
    DataQuery,
    LaboratoryResult,
    MedicationExposure,
    MicrobiologyResult,
    Outcome,
    Patient,
    ProtocolDeviation,
    ScreeningLog,
    Site,
    StudyEncounter,
    User,
)
from irtcri.hub import schemas as S
from irtcri.schemas.dictionary import check_cross_field, load_dictionary, validate_value
from irtcri.schemas.enums import POSITIVE_LABELS


class ServiceError(Exception):
    status_code = 400


class NotFound(ServiceError):
    status_code = 404


class Forbidden(ServiceError):
    status_code = 403


class Conflict(ServiceError):
    status_code = 409


class ValidationFailed(ServiceError):
    status_code = 422

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ------------------------------------------------------------------ lookups
def get_site(session: Session, site_code: str) -> Site:
    site = session.scalar(select(Site).where(Site.site_code == site_code))
    if site is None:
        raise NotFound(f"unknown site '{site_code}'")
    return site


def get_patient(session: Session, who: Principal, study_id: str) -> Patient:
    p = session.scalar(select(Patient).where(Patient.study_id == study_id))
    if p is None or p.is_voided:
        raise NotFound(f"unknown study_id '{study_id}'")
    if not who.can_access_site(p.site_id):
        raise Forbidden("study participant belongs to another site")
    return p


def ensure_not_withdrawn(p: Patient, event_time: datetime | None) -> None:
    """No new data may be recorded for time points after a participant's withdrawal."""
    if p.withdrawn_time is not None and (event_time is None or event_time > p.withdrawn_time):
        raise ValidationFailed([f"{p.study_id} withdrew on {p.withdrawn_time.isoformat()}; "
                                "data after withdrawal cannot be recorded"])


def withdraw_patient(session: Session, who: Principal, study_id: str, data: "S.WithdrawalIn") -> Patient:
    """Record withdrawal. Nothing is erased: existing records stay (audit trail), future entry is
    blocked, and ``data_use_after_withdrawal=False`` removes the participant from datasets/exports."""
    _require(who, Perm.CORRECT_DATA)
    p = get_patient(session, who, study_id)
    if p.withdrawn_time is not None:
        raise Conflict("withdrawal already recorded; use a correction with a reason to amend it")
    before = row_snapshot(p)
    p.withdrawn_time, p.withdrawal_reason = data.withdrawn_time, data.reason
    p.data_use_after_withdrawal = data.data_use_after_withdrawal
    p.record_version += 1
    session.flush()
    audit(session, who, "withdraw", "patient", p.id, before=before, after=row_snapshot(p), reason=data.reason)
    return p


def get_episode(session: Session, who: Principal, episode_code: str, patient: Patient | None = None
                ) -> CatheterEpisode:
    ep = session.scalar(select(CatheterEpisode).where(CatheterEpisode.episode_code == episode_code))
    if ep is None or ep.is_voided:
        raise NotFound(f"unknown episode '{episode_code}'")
    if not who.can_access_site(ep.site_id):
        raise Forbidden("episode belongs to another site")
    if patient is not None and ep.patient_id != patient.id:
        raise ValidationFailed([f"episode {episode_code} does not belong to {patient.study_id}"])
    return ep


def _require(who: Principal, perm: Perm) -> None:
    if not who.can(perm):
        raise Forbidden(f"role {who.role} lacks permission '{perm}'")


def _add(session: Session, who: Principal, row: Base, source: str = "manual", batch_id: str | None = None,
         reason: str | None = None) -> Base:
    if hasattr(row, "recorded_by"):
        row.recorded_by = who.user_id
        row.source = source
        row.import_batch_id = batch_id
    session.add(row)
    session.flush()
    audit(session, who, "create", row.__tablename__, row.id, after=row_snapshot(row), reason=reason)
    return row


def auto_query(session: Session, site_id: str, entity_type: str, entity_id: str, field: str | None,
               message: str) -> DataQuery:
    q = DataQuery(site_id=site_id, entity_type=entity_type, entity_id=entity_id, field=field, message=message,
                  auto_generated=True)
    session.add(q)
    return q


# -------------------------------------------------------------- organisation
def create_site(session: Session, who: Principal, data: S.SiteIn) -> Site:
    _require(who, Perm.MANAGE_SITES)
    if session.scalar(select(Site).where(Site.site_code == data.site_code)):
        raise Conflict(f"site {data.site_code} exists")
    return _add(session, who, Site(**data.model_dump()))


def create_user(session: Session, who: Principal, data: S.UserIn) -> tuple[User, str]:
    _require(who, Perm.MANAGE_USERS)
    role = Role(data.role)
    site_id = get_site(session, data.site_code).id if data.site_code else None
    if role == Role.SITE_RESEARCHER and site_id is None:
        raise ValidationFailed(["SiteResearcher accounts must be bound to a site"])
    if session.scalar(select(User).where(User.username == data.username)):
        raise Conflict(f"user {data.username} exists")
    token = new_token()
    from datetime import timedelta

    from irtcri.core.config import get_settings
    from irtcri.core.timeutils import utcnow
    u = User(username=data.username, role=role.value, site_id=site_id, token_hash=hash_token(token),
             token_expires=utcnow() + timedelta(days=get_settings().token_ttl_days))
    session.add(u)
    session.flush()
    audit(session, who, "create", "app_user", u.id, after={"username": u.username, "role": u.role,
                                                            "site_id": site_id})
    return u, token


# ----------------------------------------------------------------- enrolment
def create_screening(session: Session, who: Principal, data: S.ScreeningIn, source="manual", batch_id=None):
    _require(who, Perm.ENROL)
    site = get_site(session, data.site_code)
    if not who.can_access_site(site.id):
        raise Forbidden("other site")
    d = data.model_dump(exclude={"site_code"})
    return _add(session, who, ScreeningLog(site_id=site.id, **d), source, batch_id)


def next_study_id(session: Session, site: Site) -> str:
    n = session.scalar(select(func.count()).select_from(Patient).where(Patient.site_id == site.id)) or 0
    while True:
        n += 1
        sid = f"{site.site_code}-{n:04d}"
        if not session.scalar(select(Patient.id).where(Patient.study_id == sid)):
            return sid


def create_patient(session: Session, who: Principal, data: S.PatientIn, source="manual", batch_id=None) -> Patient:
    _require(who, Perm.ENROL)
    site = get_site(session, data.site_code)
    if not who.can_access_site(site.id):
        raise Forbidden("cannot enrol at another site")
    d = load_dictionary()
    errors, flags = [], []
    for f in ("age_years", "bmi"):
        v = getattr(data, f)
        if v is not None:
            r = validate_value(d.get(f), v)
            errors += [f"{f}: {e}" for e in r.errors]
            flags += [f"{f}: {x}" for x in r.flags]
    if errors:
        raise ValidationFailed(errors)
    study_id = data.study_id or next_study_id(session, site)
    if not study_id.startswith(site.site_code + "-"):
        raise ValidationFailed([f"study_id must start with site code '{site.site_code}-'"])
    if session.scalar(select(Patient.id).where(Patient.study_id == study_id)):
        raise Conflict(f"study_id {study_id} already exists")
    vals = data.model_dump(exclude={"site_code", "study_id", "screening_number"})
    p = Patient(study_id=study_id, site_id=site.id, dq_flags=flags or None, **vals)
    _add(session, who, p, source, batch_id)
    if data.screening_number:
        sl = session.scalar(select(ScreeningLog).where(ScreeningLog.site_id == site.id,
                                                       ScreeningLog.screening_number == data.screening_number))
        if sl is None:
            raise ValidationFailed([f"screening number {data.screening_number} not in screening log"])
        sl.patient_id = p.id
    for fl in flags:
        auto_query(session, site.id, "patient", p.id, fl.split(":")[0], f"Please verify: {fl}")
    return p


def create_encounter(session: Session, who: Principal, data: S.EncounterIn, source="manual", batch_id=None):
    _require(who, Perm.ENROL)
    p = get_patient(session, who, data.study_id)
    vals = data.model_dump(exclude={"study_id"})
    return _add(session, who, StudyEncounter(patient_id=p.id, site_id=p.site_id, **vals), source, batch_id)


def create_episode(session: Session, who: Principal, data: S.EpisodeIn, source="manual", batch_id=None
                   ) -> CatheterEpisode:
    _require(who, Perm.ENROL)
    p = get_patient(session, who, data.study_id)
    n = session.scalar(select(func.count()).select_from(CatheterEpisode)
                       .where(CatheterEpisode.patient_id == p.id)) or 0
    code = data.episode_code or f"{p.study_id}-C{n + 1}"
    if session.scalar(select(CatheterEpisode.id).where(CatheterEpisode.episode_code == code)):
        raise Conflict(f"episode {code} exists")
    vals = data.model_dump(exclude={"study_id", "episode_code"})
    vals["follow_up_start_time"] = vals["follow_up_start_time"] or vals["insertion_time"]
    ep = CatheterEpisode(episode_code=code, patient_id=p.id, site_id=p.site_id, **vals)
    # overlapping episodes for the same patient are allowed (several lines) but flagged for review
    return _add(session, who, ep, source, batch_id)


# ------------------------------------------------------------- clinical data
def create_measurement(session: Session, who: Principal, data: S.MeasurementIn, source: str | None = None,
                       batch_id: str | None = None) -> ClinicalObservation | LaboratoryResult:
    _require(who, Perm.WRITE_CLINICAL)
    d = load_dictionary()
    if data.variable not in d:
        resolved = d.resolve(data.variable)
        if resolved is None:
            raise ValidationFailed([f"unknown variable '{data.variable}'"])
        data = data.model_copy(update={"variable": resolved})
    var = d.get(data.variable)
    if var.entity not in ("observation", "laboratory"):
        raise ValidationFailed([f"variable '{var.name}' belongs to entity '{var.entity}', not a measurement"])
    p = get_patient(session, who, data.study_id)
    ensure_not_withdrawn(p, data.event_time)
    ep = get_episode(session, who, data.episode_code, p) if data.episode_code else None
    r = validate_value(var, data.value, data.unit, data.missing_reason, d)
    if not r.ok:
        raise ValidationFailed([f"{var.name}: {e}" for e in r.errors])
    common = dict(patient_id=p.id, episode_id=ep.id if ep else None, variable=var.name, unit=var.unit,
                  original_value=None if data.value is None else str(data.value), original_unit=data.unit,
                  missing_reason=data.missing_reason, event_time=data.event_time, source_time=data.source_time,
                  source_ref=data.source_ref, dq_flags=r.flags or None)
    if var.entity == "observation":
        row = ClinicalObservation(**common)
        if var.type in ("float", "int"):
            row.value_num = r.value
        elif r.value is not None:
            row.value_text = str(r.value)
    else:
        row = LaboratoryResult(**common, value_num=r.value, comparator=data.comparator, loinc_code=data.loinc_code)
    _add(session, who, row, source or data.source.value, batch_id)
    for fl in r.flags:
        if fl.startswith("implausible"):
            auto_query(session, p.site_id, row.__tablename__, row.id, var.name, f"Please verify {var.name}: {fl}")
    if var.name in ("sbp", "dbp", "map") and r.value is not None:
        _cross_field(session, p, row)
    return row


def _cross_field(session: Session, p: Patient, row: ClinicalObservation) -> None:
    same = session.scalars(select(ClinicalObservation).where(
        ClinicalObservation.patient_id == p.id, ClinicalObservation.event_time == row.event_time,
        ClinicalObservation.variable.in_(["sbp", "dbp", "map"]), ClinicalObservation.is_voided.is_(False))).all()
    vals = {o.variable: o.value_num for o in same if o.value_num is not None}
    for msg in check_cross_field(vals):
        row.dq_flags = (row.dq_flags or []) + [msg]
        auto_query(session, p.site_id, "clinical_observation", row.id, row.variable, f"Cross-field check: {msg}")


def create_medication(session: Session, who: Principal, data: S.MedicationIn, source=None, batch_id=None):
    _require(who, Perm.WRITE_CLINICAL)
    p = get_patient(session, who, data.study_id)
    ensure_not_withdrawn(p, data.start_time)
    ep = get_episode(session, who, data.episode_code, p) if data.episode_code else None
    vals = data.model_dump(exclude={"study_id", "episode_code", "source", "source_ref"})
    row = MedicationExposure(patient_id=p.id, episode_id=ep.id if ep else None, source_ref=data.source_ref, **vals)
    return _add(session, who, row, source or data.source.value, batch_id)


def create_microbiology(session: Session, who: Principal, data: S.MicrobiologyIn, source=None, batch_id=None):
    _require(who, Perm.WRITE_CLINICAL)
    p = get_patient(session, who, data.study_id)
    ensure_not_withdrawn(p, data.event_time)
    ep = get_episode(session, who, data.episode_code, p) if data.episode_code else None
    vals = data.model_dump(exclude={"study_id", "episode_code", "source", "source_ref"})
    row = MicrobiologyResult(patient_id=p.id, episode_id=ep.id if ep else None, source_ref=data.source_ref, **vals)
    return _add(session, who, row, source or data.source.value, batch_id)


def create_assessment(session: Session, who: Principal, data: S.AssessmentIn, source=None, batch_id=None):
    _require(who, Perm.WRITE_CLINICAL)
    p = get_patient(session, who, data.study_id)
    ensure_not_withdrawn(p, data.event_time)
    ep = get_episode(session, who, data.episode_code, p)
    d = load_dictionary()
    errors, flags = [], []
    for f in ("erythema_mm", "induration_mm"):
        v = getattr(data, f)
        if v is not None:
            r = validate_value(d.get(f), v)
            errors += [f"{f}: {e}" for e in r.errors]
            flags += [f"{f}: {x}" for x in r.flags]
    if errors:
        raise ValidationFailed(errors)
    vals = data.model_dump(exclude={"study_id", "episode_code", "source"})
    row = ClinicalAssessment(episode_id=ep.id, patient_id=p.id, dq_flags=flags or None, **vals)
    return _add(session, who, row, source or data.source.value, batch_id)


def create_outcome(session: Session, who: Principal, data: S.OutcomeIn, source=None, batch_id=None) -> Outcome:
    _require(who, Perm.WRITE_OUTCOME)
    p = get_patient(session, who, data.study_id)
    ep = get_episode(session, who, data.episode_code, p)
    for f in ("clinical_recognition_time", "onset_time_reference"):
        t = getattr(data, f)
        if t is not None and t < ep.insertion_time:
            raise ValidationFailed([f"{f} precedes catheter insertion"])
    current = session.scalars(select(Outcome).where(Outcome.episode_id == ep.id,
                                                    Outcome.is_voided.is_(False))).all()
    if current:
        raise Conflict("episode already has an outcome; use a correction (PATCH) with a reason")
    vals = data.model_dump(exclude={"study_id", "episode_code", "source"})
    row = Outcome(episode_id=ep.id, patient_id=p.id, is_event=data.outcome_type in POSITIVE_LABELS, **vals)
    return _add(session, who, row, source or data.source.value, batch_id)


def create_deviation(session: Session, who: Principal, data: S.DeviationIn, source="manual", batch_id=None):
    _require(who, Perm.WRITE_CLINICAL)
    site = get_site(session, data.site_code)
    if not who.can_access_site(site.id):
        raise Forbidden("other site")
    p = get_patient(session, who, data.study_id) if data.study_id else None
    ep = get_episode(session, who, data.episode_code, p) if data.episode_code else None
    vals = data.model_dump(exclude={"site_code", "study_id", "episode_code"})
    row = ProtocolDeviation(site_id=site.id, patient_id=p.id if p else None, episode_id=ep.id if ep else None,
                            **vals)
    return _add(session, who, row, source, batch_id)


# ------------------------------------------------------------- corrections
CORRECTABLE: dict[str, type[Base]] = {m.__tablename__: m for m in (
    Patient, StudyEncounter, CatheterEpisode, ClinicalObservation, LaboratoryResult, MedicationExposure,
    MicrobiologyResult, ClinicalAssessment, Outcome, ProtocolDeviation, ScreeningLog)}
IMMUTABLE_FIELDS = {"id", "study_id", "patient_id", "site_id", "recorded_time", "recorded_by", "record_version",
                    "is_voided", "void_reason", "source", "import_batch_id", "episode_code"}


def _site_of(session: Session, row: Base) -> str | None:
    if hasattr(row, "site_id"):
        return row.site_id
    if hasattr(row, "patient_id"):
        return session.get(Patient, row.patient_id).site_id
    return None


def correct_record(session: Session, who: Principal, entity_type: str, entity_id: str, changes: dict[str, Any],
                   reason: str, expected_version: int | None = None) -> Base:
    """Field-level correction with reason. Previous values are preserved in the audit trail."""
    _require(who, Perm.CORRECT_DATA)
    Model = CORRECTABLE.get(entity_type)
    if Model is None:
        raise NotFound(f"entity type '{entity_type}' not correctable")
    row = session.get(Model, entity_id)
    if row is None:
        raise NotFound(f"{entity_type} {entity_id} not found")
    if not who.can_access_site(_site_of(session, row)):
        raise Forbidden("other site")
    if row.is_voided:
        raise Conflict("record is voided")
    if expected_version is not None and expected_version != row.record_version:
        raise Conflict(f"record changed (version {row.record_version}); reload before correcting")
    bad = set(changes) & IMMUTABLE_FIELDS
    unknown = {k for k in changes if not hasattr(Model, k)}
    if bad or unknown:
        raise ValidationFailed([f"fields not correctable: {sorted(bad | unknown)}"])
    before = row_snapshot(row)
    if Model in (ClinicalObservation, LaboratoryResult) and ("value_num" in changes or "variable" in changes):
        var = load_dictionary().get(changes.get("variable", row.variable))
        r = validate_value(var, changes.get("value_num", row.value_num))
        if not r.ok:
            raise ValidationFailed(r.errors)
        row.dq_flags = r.flags or None
    for k, v in changes.items():
        col = Model.__table__.c.get(k)
        if col is not None and isinstance(col.type, UTCDateTime) and isinstance(v, str):
            v = parse_timestamp(v, None, k)
        elif col is not None and isinstance(col.type, Date) and isinstance(v, str):
            v = date.fromisoformat(v)
        setattr(row, k, v)
    if isinstance(row, Outcome) and "outcome_type" in changes:
        row.is_event = row.outcome_type in POSITIVE_LABELS
    row.record_version += 1
    session.flush()
    audit(session, who, "update", entity_type, entity_id, before=before, after=row_snapshot(row), reason=reason)
    return row


def void_record(session: Session, who: Principal, entity_type: str, entity_id: str, reason: str) -> Base:
    _require(who, Perm.CORRECT_DATA)
    Model = CORRECTABLE.get(entity_type)
    if Model is None:
        raise NotFound(f"entity type '{entity_type}' not voidable")
    row = session.get(Model, entity_id)
    if row is None:
        raise NotFound(f"{entity_type} {entity_id} not found")
    if not who.can_access_site(_site_of(session, row)):
        raise Forbidden("other site")
    row.is_voided, row.void_reason = True, reason
    row.record_version += 1
    session.flush()
    audit(session, who, "void", entity_type, entity_id, reason=reason)
    return row


def as_dict(row: Base) -> dict[str, Any]:
    out = {}
    for k, v in row_snapshot(row).items():
        out[k] = v
    return out


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
