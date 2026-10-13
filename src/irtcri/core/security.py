"""Role-based access control.

Roles and permissions are deliberately explicit so that ethics committees and
data-protection officers can review them (docs/security_governance.md).
"""

from __future__ import annotations

import hashlib
import secrets
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "Admin"
    DATA_MANAGER = "DataManager"
    SITE_RESEARCHER = "SiteResearcher"
    ANNOTATOR = "Annotator"
    STATISTICIAN = "Statistician"
    ML_RESEARCHER = "MLResearcher"
    CLINICIAN_REVIEWER = "ClinicianReviewer"
    READ_ONLY_AUDITOR = "ReadOnlyAuditor"


class Perm(StrEnum):
    MANAGE_USERS = "manage_users"
    MANAGE_SITES = "manage_sites"
    ENROL = "enrol"                         # create patients / episodes / screening entries
    WRITE_CLINICAL = "write_clinical"       # observations, labs, meds, micro, assessments
    WRITE_OUTCOME = "write_outcome"
    UPLOAD_IMAGES = "upload_images"
    READ_CLINICAL = "read_clinical"
    READ_IMAGES = "read_images"
    CORRECT_DATA = "correct_data"           # corrections / voiding with reason
    MANAGE_QUERIES = "manage_queries"
    RAISE_QUERY = "raise_query"
    ANNOTATE = "annotate"
    ADJUDICATE = "adjudicate"
    MANAGE_ANNOTATION = "manage_annotation"  # create reader assignments
    EXPORT_DATA = "export_data"
    VIEW_DQ = "view_dq"
    READ_AUDIT = "read_audit"
    MANAGE_MODELS = "manage_models"
    WRITE_PREDICTIONS = "write_predictions"
    VIEW_PREDICTIONS = "view_predictions"
    ALL_SITES = "all_sites"                 # not restricted to own site


_P = Perm
ROLE_PERMISSIONS: dict[Role, frozenset[Perm]] = {
    Role.ADMIN: frozenset(Perm),
    Role.DATA_MANAGER: frozenset({
        _P.MANAGE_SITES, _P.ENROL, _P.WRITE_CLINICAL, _P.WRITE_OUTCOME, _P.UPLOAD_IMAGES,
        _P.READ_CLINICAL, _P.READ_IMAGES, _P.CORRECT_DATA, _P.MANAGE_QUERIES, _P.RAISE_QUERY,
        _P.EXPORT_DATA, _P.VIEW_DQ, _P.VIEW_PREDICTIONS, _P.ALL_SITES, _P.MANAGE_ANNOTATION,
    }),
    Role.SITE_RESEARCHER: frozenset({
        _P.ENROL, _P.WRITE_CLINICAL, _P.WRITE_OUTCOME, _P.UPLOAD_IMAGES, _P.READ_CLINICAL,
        _P.READ_IMAGES, _P.CORRECT_DATA, _P.RAISE_QUERY, _P.VIEW_DQ,
    }),
    # Annotators only see what the blinding profile releases (enforced in annotation router).
    Role.ANNOTATOR: frozenset({_P.ANNOTATE}),
    Role.STATISTICIAN: frozenset({
        _P.READ_CLINICAL, _P.READ_IMAGES, _P.EXPORT_DATA, _P.VIEW_DQ, _P.VIEW_PREDICTIONS,
        _P.RAISE_QUERY, _P.ALL_SITES,
    }),
    Role.ML_RESEARCHER: frozenset({
        _P.READ_CLINICAL, _P.READ_IMAGES, _P.EXPORT_DATA, _P.MANAGE_MODELS, _P.WRITE_PREDICTIONS,
        _P.VIEW_PREDICTIONS, _P.VIEW_DQ, _P.ALL_SITES,
    }),
    Role.CLINICIAN_REVIEWER: frozenset({
        _P.READ_CLINICAL, _P.READ_IMAGES, _P.ADJUDICATE, _P.ANNOTATE, _P.VIEW_PREDICTIONS,
        _P.RAISE_QUERY, _P.ALL_SITES,
    }),
    Role.READ_ONLY_AUDITOR: frozenset({_P.READ_AUDIT, _P.VIEW_DQ, _P.ALL_SITES}),
}


def has_perm(role: Role | str, perm: Perm) -> bool:
    return perm in ROLE_PERMISSIONS[Role(role)]


def new_token() -> str:
    """High-entropy bearer token, shown once to the user."""
    return "irt_" + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # Tokens are 256-bit random values, so a fast hash is adequate (no dictionary attack surface).
    return hashlib.sha256(token.encode()).hexdigest()


from dataclasses import dataclass  # noqa: E402


@dataclass(frozen=True)
class Principal:
    """The authenticated actor of a request / CLI job."""
    user_id: str | None
    username: str
    role: Role
    site_id: str | None = None

    def can(self, perm: Perm) -> bool:
        return has_perm(self.role, perm)

    def can_access_site(self, site_id: str | None) -> bool:
        return self.can(Perm.ALL_SITES) or site_id is None or site_id == self.site_id


SYSTEM_PRINCIPAL = Principal(user_id=None, username="system", role=Role.ADMIN)
