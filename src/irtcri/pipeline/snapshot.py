"""Read the Data Hub into pandas DataFrames (one implementation used by the
DQ dashboard, dataset builder and statistical exports)."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from irtcri.db import models as M

TABLES: dict[str, type] = {
    "site": M.Site, "screening_log": M.ScreeningLog, "patient": M.Patient, "study_encounter": M.StudyEncounter,
    "catheter_episode": M.CatheterEpisode, "clinical_observation": M.ClinicalObservation,
    "laboratory_result": M.LaboratoryResult, "medication_exposure": M.MedicationExposure,
    "microbiology_result": M.MicrobiologyResult, "clinical_assessment": M.ClinicalAssessment,
    "thermal_image": M.ThermalImage, "rgb_image": M.RGBImage, "image_pair": M.ImagePair,
    "annotation": M.Annotation, "adjudication": M.Adjudication, "outcome": M.Outcome,
    "protocol_deviation": M.ProtocolDeviation, "data_query": M.DataQuery, "import_batch": M.ImportBatch,
    "model_prediction": M.ModelPrediction, "model_version": M.ModelVersion,
    "analysis_record": M.AnalysisRecord,
}
# never exported from the hub
SENSITIVE_COLUMNS = {"app_user": {"token_hash"}}


def load_tables(bind: Session | Engine, include_voided: bool = False, site_ids: list[str] | None = None,
                names: list[str] | None = None) -> dict[str, pd.DataFrame]:
    session = bind if isinstance(bind, Session) else Session(bind)
    out: dict[str, pd.DataFrame] = {}
    for name, Model in TABLES.items():
        if names and name not in names:
            continue
        cols = [c for c in Model.__table__.columns]
        rows = session.execute(select(*cols)).all()
        df = pd.DataFrame(rows, columns=[c.name for c in cols])
        for c in cols:
            if c.type.__class__.__name__ == "UTCDateTime":
                df[c.name] = pd.to_datetime(df[c.name], utc=True)
        if not include_voided and "is_voided" in df.columns:
            df = df[~df["is_voided"].astype(bool)]
        if site_ids is not None:
            if "site_id" in df.columns:
                df = df[df["site_id"].isin(site_ids)]
            elif name == "site":
                df = df[df["id"].isin(site_ids)]
            elif "patient_id" in df.columns and "patient" in out:
                df = df[df["patient_id"].isin(out["patient"]["id"])]
        out[name] = df.reset_index(drop=True)
    if not isinstance(bind, Session):
        session.close()
    return out


def exclude_withdrawn_without_consent(tables: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], int]:
    """Drop participants who withdrew WITHOUT consent to further data use from an export.

    The hub keeps their records (audit trail); they simply never leave it again.
    """
    p = tables.get("patient")
    if p is None or p.empty:
        return tables, 0
    gone = set(p.loc[p["withdrawn_time"].notna() & ~p["data_use_after_withdrawal"].fillna(False).astype(bool), "id"])
    if not gone:
        return tables, 0
    out = {}
    for name, df in tables.items():
        if name == "patient":
            out[name] = df[~df["id"].isin(gone)]
        elif "patient_id" in df.columns:
            out[name] = df[~df["patient_id"].isin(gone)]
        else:
            out[name] = df
    return out, len(gone)
