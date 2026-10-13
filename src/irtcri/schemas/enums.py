"""Canonical controlled vocabularies shared by the Data Hub, pipeline and ML code.

Changing a value here is a *schema change*: add a migration and bump the
data-dictionary version.
"""

from enum import StrEnum


class Sex(StrEnum):
    FEMALE = "female"
    MALE = "male"
    OTHER = "other"
    UNKNOWN = "unknown"


class ScreeningOutcome(StrEnum):
    ENROLLED = "enrolled"
    NOT_ELIGIBLE = "not_eligible"
    DECLINED = "declined"
    NOT_APPROACHED = "not_approached"
    OTHER = "other"


class CatheterType(StrEnum):
    CVC = "CVC"                 # non-tunnelled central venous catheter
    HD_CVC = "HD_CVC"           # haemodialysis catheter (tunnelled or non-tunnelled)
    PORT = "PORT"               # totally implanted port
    PICC = "PICC"
    OTHER = "OTHER"


class InsertionSite(StrEnum):
    IJ_RIGHT = "internal_jugular_right"
    IJ_LEFT = "internal_jugular_left"
    SUBCLAVIAN_RIGHT = "subclavian_right"
    SUBCLAVIAN_LEFT = "subclavian_left"
    FEMORAL_RIGHT = "femoral_right"
    FEMORAL_LEFT = "femoral_left"
    CHEST_WALL_RIGHT = "chest_wall_right"
    CHEST_WALL_LEFT = "chest_wall_left"
    ARM_RIGHT = "arm_right"
    ARM_LEFT = "arm_left"
    OTHER = "other"


class DataSource(StrEnum):
    MANUAL = "manual"
    IMPORT = "import"
    API = "api"
    EHR = "ehr"           # e.g. FHIR adapter
    DEVICE = "device"     # camera / monitor


class InfectionLabel(StrEnum):
    """Reference / annotation categories (ordered roughly by severity)."""
    NO_INFECTION = "no_infection"
    POSSIBLE_INFECTION = "possible_infection"
    EXIT_SITE_INFECTION = "exit_site_infection"
    TUNNEL_INFECTION = "tunnel_infection"
    POCKET_INFECTION = "pocket_infection"      # ports
    CRBSI = "crbsi"
    UNCERTAIN = "uncertain_insufficient_data"


#: Labels that count as a *positive* CRI/ESI event for the primary binary endpoint.
POSITIVE_LABELS = frozenset({
    InfectionLabel.EXIT_SITE_INFECTION, InfectionLabel.TUNNEL_INFECTION,
    InfectionLabel.POCKET_INFECTION, InfectionLabel.CRBSI,
})


class ClinicalImpression(StrEnum):
    NO_SIGNS = "no_signs"
    SUSPECTED_INFECTION = "suspected_infection"
    CONFIRMED_INFECTION = "confirmed_infection"
    NOT_ASSESSABLE = "not_assessable"


class QCStatus(StrEnum):
    PENDING = "PENDING"
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


class ImageModality(StrEnum):
    THERMAL = "thermal"
    RGB = "rgb"


class ImageView(StrEnum):
    FRONTAL = "frontal"           # perpendicular to skin over exit site
    OBLIQUE_LEFT = "oblique_left"
    OBLIQUE_RIGHT = "oblique_right"
    CONTRALATERAL = "contralateral_reference"
    OTHER = "other"


class ReaderRole(StrEnum):
    READER_1 = "reader_1"
    READER_2 = "reader_2"
    ADJUDICATOR = "adjudicator"


class AnnotationStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    SUPERSEDED = "superseded"


class MicroGrowth(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    CONTAMINANT = "contaminant"
    PENDING = "pending"


class SpecimenType(StrEnum):
    BLOOD_PERIPHERAL = "blood_peripheral"
    BLOOD_CATHETER = "blood_catheter"
    EXIT_SITE_SWAB = "exit_site_swab"
    CATHETER_TIP = "catheter_tip"
    PUS = "pus"
    OTHER = "other"


class DeviationSeverity(StrEnum):
    MINOR = "minor"
    MAJOR = "major"


class QueryStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    CLOSED = "closed"


class PredictionMode(StrEnum):
    RETROSPECTIVE_REPLAY = "retrospective_replay"
    SILENT_PROSPECTIVE = "silent_prospective"     # computed but not shown to clinicians
    PROSPECTIVE_ALERT = "prospective_alert"       # requires validation + approval


class ModelStatus(StrEnum):
    EXPERIMENTAL = "experimental"
    CANDIDATE = "candidate"
    VALIDATED_LOCKED = "validated_locked"
    RETIRED = "retired"
