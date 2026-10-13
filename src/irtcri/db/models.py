"""Canonical relational schema of the IRT-CRI Data Hub.

Design principles (see docs/data_model.md):

* Pseudonymous: no names, MRNs, addresses or dates of birth. ``Patient.study_id``
  is the only subject identifier; the re-identification key is kept at the
  site, outside this system.
* Longitudinal: Patient -> StudyEncounter -> CatheterEpisode -> time-stamped
  observations / images / assessments / outcomes. A patient may have several
  catheter episodes.
* Three timestamps where relevant:
    - ``event_time``    when the thing happened/was measured (clinically true time)
    - ``source_time``   when the value existed in the source system (e.g. lab report time)
    - ``recorded_time`` when it entered this database (TrackedMixin)
  The dataset builder derives an ``available_time`` from these to prevent
  future-data leakage.
* Nothing is hard-deleted; corrections increment ``record_version`` and are
  logged with before/after values in ``audit_event``.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from irtcri.db.base import Base, IdMixin, JSONType, TrackedMixin, UTCDateTime, _now


# =============================================================== organisation
class Site(IdMixin, Base):
    __tablename__ = "site"
    site_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str | None] = mapped_column(String(64))
    setting: Mapped[str | None] = mapped_column(String(64))       # ICU, dialysis centre, oncology...
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


class User(IdMixin, Base):
    __tablename__ = "app_user"
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("site.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    token_expires: Mapped[datetime | None] = mapped_column(UTCDateTime())
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


# ================================================================ enrolment
class ScreeningLog(TrackedMixin, Base):
    """Every screened candidate (no identifiers) - basis of the cohort-flow diagram."""
    __tablename__ = "screening_log"
    site_id: Mapped[str] = mapped_column(ForeignKey("site.id"), nullable=False)
    screening_number: Mapped[str] = mapped_column(String(32), nullable=False)
    screening_date: Mapped[date] = mapped_column(Date, nullable=False)
    eligible: Mapped[bool | None] = mapped_column(Boolean)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)       # ScreeningOutcome
    exclusion_reason: Mapped[str | None] = mapped_column(String(255))
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patient.id"))
    __table_args__ = (UniqueConstraint("site_id", "screening_number"),)


class Patient(TrackedMixin, Base):
    __tablename__ = "patient"
    study_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("site.id"), nullable=False, index=True)
    enrolment_date: Mapped[date] = mapped_column(Date, nullable=False)
    consent_version: Mapped[str | None] = mapped_column(String(32))
    age_years: Mapped[int | None] = mapped_column(Integer)
    sex: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    bmi: Mapped[float | None] = mapped_column(Float)
    diabetes: Mapped[bool | None] = mapped_column(Boolean)
    chronic_kidney_disease: Mapped[bool | None] = mapped_column(Boolean)
    on_dialysis: Mapped[bool | None] = mapped_column(Boolean)
    immunosuppressed: Mapped[bool | None] = mapped_column(Boolean)
    comorbidities: Mapped[list | None] = mapped_column(JSONType)       # coded list, e.g. ICD-10 chapters
    skin_phototype: Mapped[str | None] = mapped_column(String(8))     # Fitzpatrick I-VI (RGB fairness analyses)
    withdrawn_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    withdrawal_reason: Mapped[str | None] = mapped_column(String(255))
    data_use_after_withdrawal: Mapped[bool | None] = mapped_column(Boolean)

    site: Mapped[Site] = relationship()
    episodes: Mapped[list["CatheterEpisode"]] = relationship(back_populates="patient")


class StudyEncounter(TrackedMixin, Base):
    """Hospital admission / dialysis-unit attendance period in which the patient is followed."""
    __tablename__ = "study_encounter"
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("site.id"), nullable=False)
    encounter_type: Mapped[str] = mapped_column(String(32), default="inpatient")   # inpatient|icu|outpatient_dialysis
    department: Mapped[str | None] = mapped_column(String(64))
    admission_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    discharge_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    admission_diagnosis: Mapped[str | None] = mapped_column(String(255))
    admission_diagnosis_code: Mapped[str | None] = mapped_column(String(16))       # ICD-10
    discharge_status: Mapped[str | None] = mapped_column(String(32))               # alive|died|transferred


class CatheterEpisode(TrackedMixin, Base):
    """One catheter from insertion to removal/end of follow-up. Unit of analysis for time-to-event."""
    __tablename__ = "catheter_episode"
    episode_code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id"), nullable=False, index=True)
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("study_encounter.id"))
    site_id: Mapped[str] = mapped_column(ForeignKey("site.id"), nullable=False, index=True)
    catheter_type: Mapped[str] = mapped_column(String(16), nullable=False)        # CatheterType
    insertion_site: Mapped[str] = mapped_column(String(32), nullable=False)       # InsertionSite
    tunnelled: Mapped[bool | None] = mapped_column(Boolean)
    lumens: Mapped[int | None] = mapped_column(Integer)
    insertion_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    insertion_time_precision: Mapped[str] = mapped_column(String(8), default="minute")  # minute|hour|day
    inserted_in_department: Mapped[str | None] = mapped_column(String(64))
    follow_up_start_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    removal_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    removal_reason: Mapped[str | None] = mapped_column(String(64))
    end_of_follow_up_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    end_of_follow_up_reason: Mapped[str | None] = mapped_column(String(64))
    imaging_interval_hours: Mapped[float] = mapped_column(Float, default=12.0, nullable=False)

    patient: Mapped[Patient] = relationship(back_populates="episodes")
    __table_args__ = (
        CheckConstraint("follow_up_start_time >= insertion_time", name="follow_up_after_insertion"),
    )


# ============================================================ clinical data
class ClinicalObservation(TrackedMixin, Base):
    """Repeated bedside measurements & scores in long format (variable from the data dictionary)."""
    __tablename__ = "clinical_observation"
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id"), nullable=False)
    episode_id: Mapped[str | None] = mapped_column(ForeignKey("catheter_episode.id"))
    variable: Mapped[str] = mapped_column(String(48), nullable=False)
    value_num: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(String(255))
    unit: Mapped[str | None] = mapped_column(String(24))
    original_value: Mapped[str | None] = mapped_column(String(64))
    original_unit: Mapped[str | None] = mapped_column(String(24))
    missing_reason: Mapped[str | None] = mapped_column(String(8))
    event_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (Index("ix_obs_patient_var_time", "patient_id", "variable", "event_time"),)


class LaboratoryResult(TrackedMixin, Base):
    __tablename__ = "laboratory_result"
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id"), nullable=False)
    episode_id: Mapped[str | None] = mapped_column(ForeignKey("catheter_episode.id"))
    variable: Mapped[str] = mapped_column(String(48), nullable=False)
    value_num: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(24))
    original_value: Mapped[str | None] = mapped_column(String(64))
    original_unit: Mapped[str | None] = mapped_column(String(24))
    comparator: Mapped[str | None] = mapped_column(String(2))          # '<', '>', '<=', '>=' for censored lab values
    missing_reason: Mapped[str | None] = mapped_column(String(8))
    event_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)     # specimen collection
    source_time: Mapped[datetime | None] = mapped_column(UTCDateTime())            # result reported
    loinc_code: Mapped[str | None] = mapped_column(String(16))
    __table_args__ = (Index("ix_lab_patient_var_time", "patient_id", "variable", "event_time"),)


class MedicationExposure(TrackedMixin, Base):
    __tablename__ = "medication_exposure"
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id"), nullable=False, index=True)
    episode_id: Mapped[str | None] = mapped_column(ForeignKey("catheter_episode.id"))
    drug_name: Mapped[str] = mapped_column(String(128), nullable=False)
    atc_code: Mapped[str | None] = mapped_column(String(16))
    is_antibiotic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_catheter_lock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    route: Mapped[str | None] = mapped_column(String(32))
    dose: Mapped[float | None] = mapped_column(Float)
    dose_unit: Mapped[str | None] = mapped_column(String(16))
    frequency: Mapped[str | None] = mapped_column(String(32))
    indication: Mapped[str | None] = mapped_column(String(128))    # prophylaxis|empirical|targeted|other
    start_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    source_time: Mapped[datetime | None] = mapped_column(UTCDateTime())      # order/prescription time


class MicrobiologyResult(TrackedMixin, Base):
    __tablename__ = "microbiology_result"
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id"), nullable=False, index=True)
    episode_id: Mapped[str | None] = mapped_column(ForeignKey("catheter_episode.id"))
    specimen_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)      # collection
    source_time: Mapped[datetime | None] = mapped_column(UTCDateTime())             # final report
    preliminary_time: Mapped[datetime | None] = mapped_column(UTCDateTime())        # e.g. Gram stain / flag positive
    growth: Mapped[str] = mapped_column(String(16), nullable=False)                 # MicroGrowth
    organism: Mapped[str | None] = mapped_column(String(128))
    gram_stain: Mapped[str | None] = mapped_column(String(32))
    quantitative_cfu: Mapped[float | None] = mapped_column(Float)
    time_to_positivity_h: Mapped[float | None] = mapped_column(Float)
    susceptibility: Mapped[dict | None] = mapped_column(JSONType)


class ClinicalAssessment(TrackedMixin, Base):
    """Standard-of-care exit-site examination - the *conventional* comparator."""
    __tablename__ = "clinical_assessment"
    episode_id: Mapped[str] = mapped_column(ForeignKey("catheter_episode.id"), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id"), nullable=False)
    event_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    assessor_role: Mapped[str | None] = mapped_column(String(32))     # nurse|physician|study_staff
    erythema_mm: Mapped[float | None] = mapped_column(Float)
    induration_mm: Mapped[float | None] = mapped_column(Float)
    tenderness: Mapped[bool | None] = mapped_column(Boolean)
    purulent_discharge: Mapped[bool | None] = mapped_column(Boolean)
    serous_exudate: Mapped[bool | None] = mapped_column(Boolean)
    dressing_status: Mapped[str | None] = mapped_column(String(32))
    clinical_impression: Mapped[str] = mapped_column(String(32), nullable=False)   # ClinicalImpression
    action_taken: Mapped[str | None] = mapped_column(String(128))
    comment: Mapped[str | None] = mapped_column(Text)


# ================================================================== imaging
class _ImageCommon(TrackedMixin):
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id"), nullable=False, index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("catheter_episode.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("site.id"), nullable=False)
    acquisition_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    exif_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    timepoint_label: Mapped[str | None] = mapped_column(String(32))      # e.g. D02_AM
    view: Mapped[str] = mapped_column(String(32), default="frontal", nullable=False)
    distance_cm: Mapped[float | None] = mapped_column(Float)
    camera_make: Mapped[str | None] = mapped_column(String(64))
    camera_model: Mapped[str | None] = mapped_column(String(64))
    device_id: Mapped[str | None] = mapped_column(String(64))
    operator_id: Mapped[str | None] = mapped_column(String(64))           # pseudonymous staff code
    acquisition_protocol_version: Mapped[str | None] = mapped_column(String(32))
    ingestion_channel: Mapped[str] = mapped_column(String(32), default="upload")  # upload|watch_folder|api
    original_filename: Mapped[str | None] = mapped_column(String(255))
    file_format: Mapped[str | None] = mapped_column(String(16))
    raw_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_size_bytes: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    qc_status: Mapped[str] = mapped_column(String(8), default="PENDING", nullable=False)
    qc_reasons: Mapped[list | None] = mapped_column(JSONType)
    qc_metrics: Mapped[dict | None] = mapped_column(JSONType)
    qc_config_version: Mapped[str | None] = mapped_column(String(32))
    qc_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    qc_override_status: Mapped[str | None] = mapped_column(String(8))  # human override (kept separately)
    qc_override_reason: Mapped[str | None] = mapped_column(Text)
    duplicate_of_id: Mapped[str | None] = mapped_column(String(36))
    extra_metadata: Mapped[dict | None] = mapped_column(JSONType)
    # acquisition (= XNAT imaging session) grouping thermal + RGB captured together
    acquisition_id: Mapped[str | None] = mapped_column(String(96), index=True)
    xnat_uri: Mapped[str | None] = mapped_column(String(512))     # set once the raw file is in XNAT
    xnat_synced_time: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ThermalImage(_ImageCommon, Base):
    __tablename__ = "thermal_image"
    is_radiometric: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    temperature_unit: Mapped[str | None] = mapped_column(String(8))        # degC
    emissivity: Mapped[float | None] = mapped_column(Float)
    reflected_temperature_c: Mapped[float | None] = mapped_column(Float)
    ambient_temperature_c: Mapped[float | None] = mapped_column(Float)
    relative_humidity_pct: Mapped[float | None] = mapped_column(Float)
    calibration_metadata: Mapped[dict | None] = mapped_column(JSONType)   # camera calibration date, NETD, etc.
    t_min: Mapped[float | None] = mapped_column(Float)
    t_max: Mapped[float | None] = mapped_column(Float)
    t_mean: Mapped[float | None] = mapped_column(Float)


class RGBImage(_ImageCommon, Base):
    __tablename__ = "rgb_image"
    blur_score: Mapped[float | None] = mapped_column(Float)


class ImagePair(IdMixin, Base):
    """Links a thermal and an RGB acquisition of the same site/time, with registration."""
    __tablename__ = "image_pair"
    thermal_image_id: Mapped[str] = mapped_column(ForeignKey("thermal_image.id"), nullable=False)
    rgb_image_id: Mapped[str] = mapped_column(ForeignKey("rgb_image.id"), nullable=False)
    pairing_method: Mapped[str] = mapped_column(String(32), default="same_capture")   # same_capture|time_window|manual
    time_difference_s: Mapped[float | None] = mapped_column(Float)
    registration_method: Mapped[str | None] = mapped_column(String(32))   # none|affine_control_points|camera_fixed
    registration_matrix: Mapped[list | None] = mapped_column(JSONType)     # 2x3 affine (rgb -> thermal pixels)
    registration_error_px: Mapped[float | None] = mapped_column(Float)
    created_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    __table_args__ = (UniqueConstraint("thermal_image_id", "rgb_image_id"),)


class DerivedImage(IdMixin, Base):
    """Resized/normalised/registered derivative. Raw images are never modified."""
    __tablename__ = "derived_image"
    parent_modality: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_image_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    parent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    derivation: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSONType, nullable=False)
    preprocessing_version: Mapped[str] = mapped_column(String(32), nullable=False)
    code_version: Mapped[str | None] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    __table_args__ = (UniqueConstraint("parent_image_id", "derivation", "preprocessing_version"),)


# ============================================================== annotation
class Annotation(IdMixin, Base):
    """Blinded, versioned reader annotation of one thermal image (optionally with its RGB pair)."""
    __tablename__ = "annotation"
    thermal_image_id: Mapped[str] = mapped_column(ForeignKey("thermal_image.id"), nullable=False, index=True)
    rgb_image_id: Mapped[str | None] = mapped_column(ForeignKey("rgb_image.id"))
    annotator_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    reader_role: Mapped[str] = mapped_column(String(16), nullable=False)        # ReaderRole
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(String(36))
    blinding_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    insertion_point: Mapped[dict | None] = mapped_column(JSONType)               # {"x":..,"y":..} thermal pixels
    roi: Mapped[dict | None] = mapped_column(JSONType)  # {"type":"polygon","points":[[x,y],..]}
    reference_roi: Mapped[dict | None] = mapped_column(JSONType)                 # healthy-skin reference region
    mask_object_key: Mapped[str | None] = mapped_column(String(512))
    label: Mapped[str | None] = mapped_column(String(32))                        # InfectionLabel
    confidence: Mapped[int | None] = mapped_column(Integer)                      # 1 (very unsure) .. 5 (certain)
    image_quality_adequate: Mapped[bool | None] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(Text)
    started_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    submitted_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (CheckConstraint("confidence IS NULL OR (confidence BETWEEN 1 AND 5)", name="confidence_1_5"),)


class AnnotationAssignment(IdMixin, Base):
    __tablename__ = "annotation_assignment"
    thermal_image_id: Mapped[str] = mapped_column(ForeignKey("thermal_image.id"), nullable=False)
    annotator_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    reader_role: Mapped[str] = mapped_column(String(16), nullable=False)
    blinding_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    created_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    __table_args__ = (UniqueConstraint("thermal_image_id", "reader_role"),)


class Adjudication(IdMixin, Base):
    """Final reference label for an image (image-level) or an episode event (episode-level)."""
    __tablename__ = "adjudication"
    level: Mapped[str] = mapped_column(String(16), nullable=False)                 # image|episode
    thermal_image_id: Mapped[str | None] = mapped_column(ForeignKey("thermal_image.id"))
    episode_id: Mapped[str | None] = mapped_column(ForeignKey("catheter_episode.id"))
    reader1_annotation_id: Mapped[str | None] = mapped_column(ForeignKey("annotation.id"))
    reader2_annotation_id: Mapped[str | None] = mapped_column(ForeignKey("annotation.id"))
    adjudicator_id: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    method: Mapped[str] = mapped_column(String(32), nullable=False)                # agreement|adjudicator|committee
    final_label: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    criteria_version: Mapped[str | None] = mapped_column(String(32))
    decided_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ================================================================ outcomes
class Outcome(TrackedMixin, Base):
    """Episode-level reference outcome (the ground truth for the primary endpoint).

    ``clinical_recognition_time`` is when *conventional* care first recognised
    the infection (comparator for AI lead time). ``onset_time_reference`` is the
    adjudicated earliest time infection was present (may be retrospective).
    """
    __tablename__ = "outcome"
    episode_id: Mapped[str] = mapped_column(ForeignKey("catheter_episode.id"), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id"), nullable=False)
    outcome_type: Mapped[str] = mapped_column(String(32), nullable=False)        # InfectionLabel
    is_event: Mapped[bool] = mapped_column(Boolean, nullable=False)
    clinical_recognition_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    onset_time_reference: Mapped[datetime | None] = mapped_column(UTCDateTime())
    treatment_start_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resolution_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    criteria_met: Mapped[dict | None] = mapped_column(JSONType)                  # e.g. IDSA/CDC criteria flags
    criteria_version: Mapped[str | None] = mapped_column(String(32))
    adjudication_id: Mapped[str | None] = mapped_column(ForeignKey("adjudication.id"))
    status: Mapped[str] = mapped_column(String(16), default="provisional", nullable=False)  # provisional|adjudicated
    catheter_removed_due_to_infection: Mapped[bool | None] = mapped_column(Boolean)
    death: Mapped[bool | None] = mapped_column(Boolean)


# ============================================================= governance
class ProtocolDeviation(TrackedMixin, Base):
    __tablename__ = "protocol_deviation"
    site_id: Mapped[str] = mapped_column(ForeignKey("site.id"), nullable=False)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patient.id"))
    episode_id: Mapped[str | None] = mapped_column(ForeignKey("catheter_episode.id"))
    category: Mapped[str] = mapped_column(String(48), nullable=False)  # missed_imaging|consent|...
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    event_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    corrective_action: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)


class DataQuery(IdMixin, Base):
    """Data-management query raised against a record (classic CRF query workflow)."""
    __tablename__ = "data_query"
    site_id: Mapped[str] = mapped_column(ForeignKey("site.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    field: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    raised_by: Mapped[str | None] = mapped_column(String(36))
    raised_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    response: Mapped[str | None] = mapped_column(Text)
    closed_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ImportBatch(IdMixin, Base):
    __tablename__ = "import_batch"
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("site.id"))
    timezone_assumed: Mapped[str | None] = mapped_column(String(64))
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_accepted: Mapped[int] = mapped_column(Integer, default=0)
    rows_flagged: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    issues: Mapped[list | None] = mapped_column(JSONType)       # [{row, column, severity, message}]
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    imported_by: Mapped[str | None] = mapped_column(String(36))
    imported_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


class AuditEvent(IdMixin, Base):
    """Append-only audit trail (writes, corrections, exports, sensitive reads, logins)."""
    __tablename__ = "audit_event"
    event_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36))
    username: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(32), nullable=False)    # create|update|void|read|export|login|...
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36))
    before: Mapped[dict | None] = mapped_column(JSONType)
    after: Mapped[dict | None] = mapped_column(JSONType)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(36))
    client_ip: Mapped[str | None] = mapped_column(String(64))


# ==================================================================== ML
class DatasetVersion(IdMixin, Base):
    __tablename__ = "dataset_version"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)    # raw|validated|longitudinal|ml|locked
    manifest: Mapped[dict] = mapped_column(JSONType, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    __table_args__ = (UniqueConstraint("name", "version"),)


class ModelVersion(IdMixin, Base):
    """Model registry entry / model card."""
    __tablename__ = "model_version"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="experimental", nullable=False)
    training_dataset_version: Mapped[str | None] = mapped_column(String(96))
    code_version: Mapped[str | None] = mapped_column(String(64))
    hyperparameters: Mapped[dict | None] = mapped_column(JSONType)
    intended_use: Mapped[str | None] = mapped_column(Text)
    limitations: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict | None] = mapped_column(JSONType)
    threshold: Mapped[float | None] = mapped_column(Float)
    threshold_rule: Mapped[str | None] = mapped_column(String(128))
    calibration: Mapped[dict | None] = mapped_column(JSONType)
    validation_population: Mapped[str | None] = mapped_column(Text)
    artifact_uri: Mapped[str | None] = mapped_column(String(512))
    approval_reference: Mapped[str | None] = mapped_column(String(128))
    created_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    __table_args__ = (UniqueConstraint("name", "version"),)


class ModelPrediction(IdMixin, Base):
    __tablename__ = "model_prediction"
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_version.id"), nullable=False)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id"), nullable=False, index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("catheter_episode.id"), nullable=False, index=True)
    thermal_image_id: Mapped[str | None] = mapped_column(ForeignKey("thermal_image.id"))
    rgb_image_id: Mapped[str | None] = mapped_column(ForeignKey("rgb_image.id"))
    prediction_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)     # data cut-off time
    computed_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)                         # PredictionMode
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_positive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation_object_key: Mapped[str | None] = mapped_column(String(512))             # Grad-CAM overlay
    feature_contributions: Mapped[dict | None] = mapped_column(JSONType)
    input_provenance: Mapped[dict | None] = mapped_column(JSONType)


class AnalysisRecord(IdMixin, Base):
    """Append-only registry of manuscript analyses (what exactly produced each published result)."""
    __tablename__ = "analysis_record"
    paper_id: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    dataset_name: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_locked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    git_commit: Mapped[str | None] = mapped_column(String(64))
    statistical_config: Mapped[dict] = mapped_column(JSONType, nullable=False)
    statistical_config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(128))       # name@version
    model_card_sha256: Mapped[str | None] = mapped_column(String(64))
    environment: Mapped[dict | None] = mapped_column(JSONType)
    outputs: Mapped[dict | None] = mapped_column(JSONType)               # {relative path: sha256}
    created_by: Mapped[str | None] = mapped_column(String(64))
    date_created: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    __table_args__ = (UniqueConstraint("paper_id", "analysis_version"),)


class ExternalResource(IdMixin, Base):
    """Provenance of every artefact published to an external repository (e.g. XNAT)."""
    __tablename__ = "external_resource"
    backend: Mapped[str] = mapped_column(String(16), nullable=False)          # xnat
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)      # thermal_image, model_prediction..
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)             # raw|qc|gradcam|prediction|...
    uri: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    __table_args__ = (UniqueConstraint("backend", "uri"),)
