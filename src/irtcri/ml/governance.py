"""Governance rules for model outputs (pilot dashboard)."""

from __future__ import annotations

from irtcri import INVESTIGATIONAL_DISCLAIMER
from irtcri.core.config import Settings
from irtcri.schemas.enums import ModelStatus, PredictionMode

ALERT_TEXT = "Elevated model-estimated risk of catheter-related infection."
FORBIDDEN_PHRASES = ("patient has infection", "infection detected", "diagnosis:")


class GovernanceError(PermissionError):
    pass


def check_prediction_mode(mode: str, model_status: str, settings: Settings) -> None:
    """Prospective alerts require: feature flag + approval reference + validated, locked model."""
    if mode == PredictionMode.PROSPECTIVE_ALERT:
        if not settings.prospective_alerts_enabled:
            raise GovernanceError("prospective alerts are disabled (IRTCRI_PROSPECTIVE_ALERTS_ENABLED=false)")
        if not settings.prospective_approval_reference:
            raise GovernanceError("no ethics/approval reference configured for prospective use")
        if model_status != ModelStatus.VALIDATED_LOCKED:
            raise GovernanceError("only validated_locked models may raise prospective alerts")
    if model_status == ModelStatus.RETIRED:
        raise GovernanceError("model is retired")


def alert_payload(probability: float, threshold: float, model_label: str) -> dict:
    elevated = probability >= threshold
    return {
        "elevated_risk": elevated,
        "message": ALERT_TEXT if elevated else "Model-estimated risk below the research threshold.",
        "probability": round(probability, 4),
        "threshold": threshold,
        "model": model_label,
        "disclaimer": INVESTIGATIONAL_DISCLAIMER,
    }
