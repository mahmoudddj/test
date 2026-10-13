"""Pydantic input schemas for the Data Hub.

``extra="forbid"`` everywhere: unexpected fields (e.g. a patient's name typed
into a free column) are rejected instead of silently stored. Patients are
referenced only by pseudonymous ``study_id``; episodes by ``episode_code``.
All datetimes must be timezone-aware (``AwareDatetime``).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from irtcri.schemas.enums import (
    CatheterType,
    ClinicalImpression,
    DataSource,
    DeviationSeverity,
    ImageView,
    InfectionLabel,
    InsertionSite,
    MicroGrowth,
    ScreeningOutcome,
    Sex,
    SpecimenType,
)

STUDY_ID_RE = re.compile(r"^[A-Z0-9]{2,8}-\d{3,5}$")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SiteIn(_In):
    site_code: str = Field(pattern=r"^[A-Z0-9]{2,8}$")
    name: str
    country: str | None = None
    setting: str | None = None
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError:
            raise ValueError(f"unknown IANA timezone '{v}'") from None
        return v


class UserIn(_In):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,64}$")
    role: str
    site_code: str | None = None


class ScreeningIn(_In):
    site_code: str
    screening_number: str
    screening_date: date
    eligible: bool | None = None
    outcome: ScreeningOutcome
    exclusion_reason: str | None = None

    @model_validator(mode="after")
    def _reason(self):
        if self.outcome != ScreeningOutcome.ENROLLED and not self.exclusion_reason:
            raise ValueError("exclusion_reason is required unless outcome is 'enrolled'")
        return self


class PatientIn(_In):
    site_code: str
    study_id: str | None = Field(default=None, description="Pseudonymous ID; generated as <SITE>-NNNN if omitted")
    screening_number: str | None = None
    enrolment_date: date
    consent_version: str | None = None
    age_years: int | None = None
    sex: Sex = Sex.UNKNOWN
    bmi: float | None = None
    diabetes: bool | None = None
    chronic_kidney_disease: bool | None = None
    on_dialysis: bool | None = None
    immunosuppressed: bool | None = None
    comorbidities: list[str] | None = None
    skin_phototype: str | None = Field(default=None, pattern=r"^(I|II|III|IV|V|VI)$")

    @field_validator("study_id")
    @classmethod
    def _sid(cls, v: str | None) -> str | None:
        if v is not None and not STUDY_ID_RE.match(v):
            raise ValueError("study_id must look like 'SITE-0001' (pseudonymous; never an MRN or name)")
        return v


class EncounterIn(_In):
    study_id: str
    encounter_type: str = "inpatient"
    department: str | None = None
    admission_time: AwareDatetime
    discharge_time: AwareDatetime | None = None
    admission_diagnosis: str | None = None
    admission_diagnosis_code: str | None = None
    discharge_status: str | None = None


class EpisodeIn(_In):
    study_id: str
    episode_code: str | None = None
    encounter_id: str | None = None
    catheter_type: CatheterType
    insertion_site: InsertionSite
    tunnelled: bool | None = None
    lumens: int | None = Field(default=None, ge=1, le=5)
    insertion_time: AwareDatetime
    insertion_time_precision: str = Field(default="minute", pattern="^(minute|hour|day)$")
    inserted_in_department: str | None = None
    follow_up_start_time: AwareDatetime | None = None
    removal_time: AwareDatetime | None = None
    removal_reason: str | None = None
    end_of_follow_up_time: AwareDatetime | None = None
    end_of_follow_up_reason: str | None = None
    imaging_interval_hours: float = Field(default=12.0, gt=0, le=168)

    @model_validator(mode="after")
    def _order(self):
        if self.follow_up_start_time and self.follow_up_start_time < self.insertion_time:
            raise ValueError("follow_up_start_time precedes insertion_time")
        if self.removal_time and self.removal_time < self.insertion_time:
            raise ValueError("removal_time precedes insertion_time")
        return self


class MeasurementIn(_In):
    """Clinical observation or laboratory result (variable must exist in the data dictionary)."""
    study_id: str
    episode_code: str | None = None
    variable: str
    value: float | int | bool | str | None = None
    unit: str | None = None
    missing_reason: str | None = None
    comparator: str | None = Field(default=None, pattern=r"^(<|>|<=|>=)$")
    event_time: AwareDatetime
    source_time: AwareDatetime | None = None
    loinc_code: str | None = None
    source: DataSource = DataSource.MANUAL
    source_ref: str | None = None

    @model_validator(mode="after")
    def _times(self):
        if self.value is None and self.missing_reason is None:
            raise ValueError("either value or missing_reason must be supplied")
        if self.source_time and self.source_time < self.event_time:
            raise ValueError("source_time (e.g. result reported) cannot precede event_time (collection)")
        return self


class MedicationIn(_In):
    study_id: str
    episode_code: str | None = None
    drug_name: str
    atc_code: str | None = None
    is_antibiotic: bool | None = None
    is_catheter_lock: bool = False
    route: str | None = None
    dose: float | None = Field(default=None, ge=0)
    dose_unit: str | None = None
    frequency: str | None = None
    indication: str | None = None
    start_time: AwareDatetime
    end_time: AwareDatetime | None = None
    source_time: AwareDatetime | None = None
    source: DataSource = DataSource.MANUAL
    source_ref: str | None = None

    @model_validator(mode="after")
    def _abx(self):
        if self.is_antibiotic is None:
            # ATC J01 = antibacterials for systemic use
            self.is_antibiotic = bool(self.atc_code and self.atc_code.upper().startswith(("J01", "J04")))
        if self.end_time and self.end_time < self.start_time:
            raise ValueError("end_time precedes start_time")
        return self


class MicrobiologyIn(_In):
    study_id: str
    episode_code: str | None = None
    specimen_type: SpecimenType
    event_time: AwareDatetime
    source_time: AwareDatetime | None = None
    preliminary_time: AwareDatetime | None = None
    growth: MicroGrowth
    organism: str | None = None
    gram_stain: str | None = None
    quantitative_cfu: float | None = Field(default=None, ge=0)
    time_to_positivity_h: float | None = Field(default=None, ge=0)
    susceptibility: dict[str, str] | None = None
    source: DataSource = DataSource.MANUAL
    source_ref: str | None = None

    @model_validator(mode="after")
    def _org(self):
        if self.growth == MicroGrowth.POSITIVE and not self.organism:
            raise ValueError("organism required when growth is positive")
        return self


class AssessmentIn(_In):
    study_id: str
    episode_code: str
    event_time: AwareDatetime
    assessor_role: str | None = None
    erythema_mm: float | None = None
    induration_mm: float | None = None
    tenderness: bool | None = None
    purulent_discharge: bool | None = None
    serous_exudate: bool | None = None
    dressing_status: str | None = None
    clinical_impression: ClinicalImpression
    action_taken: str | None = None
    comment: str | None = None
    source: DataSource = DataSource.MANUAL


class OutcomeIn(_In):
    study_id: str
    episode_code: str
    outcome_type: InfectionLabel
    clinical_recognition_time: AwareDatetime | None = None
    onset_time_reference: AwareDatetime | None = None
    treatment_start_time: AwareDatetime | None = None
    resolution_time: AwareDatetime | None = None
    criteria_met: dict[str, Any] | None = None
    criteria_version: str | None = None
    status: str = Field(default="provisional", pattern="^(provisional|adjudicated)$")
    catheter_removed_due_to_infection: bool | None = None
    death: bool | None = None
    source: DataSource = DataSource.MANUAL

    @model_validator(mode="after")
    def _event_times(self):
        from irtcri.schemas.enums import POSITIVE_LABELS
        if self.outcome_type in POSITIVE_LABELS and self.clinical_recognition_time is None:
            raise ValueError("clinical_recognition_time is required for infection outcomes "
                             "(comparator for the early-detection endpoint)")
        return self


class DeviationIn(_In):
    site_code: str
    study_id: str | None = None
    episode_code: str | None = None
    category: str
    severity: DeviationSeverity
    description: str
    event_time: AwareDatetime
    corrective_action: str | None = None


class QueryIn(_In):
    entity_type: str
    entity_id: str
    field: str | None = None
    message: str


class QueryResponseIn(_In):
    response: str | None = None
    status: str = Field(pattern="^(open|answered|closed)$")


class WithdrawalIn(_In):
    withdrawn_time: AwareDatetime
    reason: str = Field(min_length=3)
    data_use_after_withdrawal: bool = Field(
        description="True: data collected before withdrawal may still be analysed (as consented). "
                    "False: participant is excluded from all future datasets and exports.")


class CorrectionIn(_In):
    changes: dict[str, Any]
    reason: str = Field(min_length=5)
    expected_version: int | None = None


class VoidIn(_In):
    reason: str = Field(min_length=5)


class QCOverrideIn(_In):
    status: str = Field(pattern="^(PASS|WARNING|FAIL)$")
    reason: str = Field(min_length=5)


class ImageMetaIn(_In):
    episode_code: str
    study_id: str | None = None
    acquisition_time: AwareDatetime
    view: ImageView = ImageView.FRONTAL
    timepoint_label: str | None = None
    distance_cm: float | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    device_id: str | None = None
    operator_id: str | None = None
    acquisition_protocol_version: str | None = None
    emissivity: float | None = None
    reflected_temperature_c: float | None = None
    ambient_temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    calibration_metadata: dict | None = None


class AnnotationIn(_In):
    insertion_point: dict | None = None
    roi: dict | None = None
    reference_roi: dict | None = None
    label: InfectionLabel | None = None
    confidence: int | None = Field(default=None, ge=1, le=5)
    image_quality_adequate: bool | None = None
    comment: str | None = None
    submit: bool = False


class AssignmentIn(_In):
    thermal_image_ids: list[str]
    annotator_username: str
    reader_role: str = Field(pattern="^(reader_1|reader_2|adjudicator)$")
    blinding_profile: str = "image_only"


class AdjudicationIn(_In):
    thermal_image_id: str
    final_label: InfectionLabel
    rationale: str | None = None
    criteria_version: str | None = None


class ModelVersionIn(_In):
    name: str
    version: str
    family: str
    training_dataset_version: str | None = None
    hyperparameters: dict | None = None
    intended_use: str
    limitations: str
    metrics: dict | None = None
    threshold: float = Field(ge=0, le=1)
    threshold_rule: str | None = None
    calibration: dict | None = None
    validation_population: str | None = None
    artifact_uri: str | None = None


class ModelStatusIn(_In):
    status: str = Field(pattern="^(experimental|candidate|validated_locked|retired)$")
    approval_reference: str | None = None
    reason: str = Field(min_length=5)


class PredictionIn(_In):
    model_name: str
    model_version: str
    episode_code: str
    thermal_image_id: str | None = None
    rgb_image_id: str | None = None
    prediction_time: AwareDatetime
    probability: float = Field(ge=0, le=1)
    mode: str = Field(default="retrospective_replay",
                      pattern="^(retrospective_replay|silent_prospective|prospective_alert)$")
    feature_contributions: dict | None = None
    explanation_object_key: str | None = None
    input_provenance: dict | None = None
