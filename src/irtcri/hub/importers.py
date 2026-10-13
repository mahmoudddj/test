"""CSV / Excel bulk import.

* Every file is checksummed and recorded as an ``ImportBatch`` with per-row
  issues (row number, column, message) - rejected rows are reported, never
  silently dropped.
* Naive timestamps are interpreted in an *explicit* timezone (default: the
  site's registered timezone); the assumption is stored on the batch.
* ``dry_run=True`` validates everything and reports issues without writing.
* Measurements may be supplied in LONG format (columns ``variable``, ``value``,
  ``unit``) or WIDE format (one column per variable; header may carry the unit
  as ``creatinine [mg/dL]``). Cells containing a missing code (ND/NA/UNK/NR)
  are stored as explicit missing values.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from irtcri.core.provenance import sha256_bytes
from irtcri.core.security import Principal
from irtcri.core.timeutils import NaiveDatetimeError, parse_timestamp
from irtcri.db.models import ImportBatch, Patient
from irtcri.hub import schemas as S
from irtcri.hub import services as svc
from irtcri.schemas.dictionary import load_dictionary

IMPORT_KINDS: dict[str, tuple[type[BaseModel], Callable]] = {
    "screening": (S.ScreeningIn, svc.create_screening),
    "patients": (S.PatientIn, svc.create_patient),
    "encounters": (S.EncounterIn, svc.create_encounter),
    "episodes": (S.EpisodeIn, svc.create_episode),
    "measurements": (S.MeasurementIn, svc.create_measurement),
    "medications": (S.MedicationIn, svc.create_medication),
    "microbiology": (S.MicrobiologyIn, svc.create_microbiology),
    "assessments": (S.AssessmentIn, svc.create_assessment),
    "outcomes": (S.OutcomeIn, svc.create_outcome),
    "deviations": (S.DeviationIn, svc.create_deviation),
}
HEADER_UNIT_RE = re.compile(r"^\s*(?P<name>[^\[\]]+?)\s*\[(?P<unit>[^\]]+)\]\s*$")
WIDE_ID_COLS = {"study_id", "episode_code", "event_time", "source_time", "source_ref"}


@dataclass
class ImportReport:
    batch: ImportBatch
    issues: list[dict[str, Any]] = field(default_factory=list)


def read_table(data: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        df = pd.read_excel(io.BytesIO(data), dtype=str)
    elif name.endswith((".csv", ".txt")):
        text = data.decode("utf-8-sig")
        sep = ";" if text.splitlines()[0].count(";") > text.splitlines()[0].count(",") else ","
        df = pd.read_csv(io.StringIO(text), dtype=str, sep=sep, keep_default_na=False)
    else:
        raise ValueError("only .csv and .xlsx files are supported")
    df.columns = [str(c).strip() for c in df.columns]
    return df.replace({"": None}).astype(object).where(pd.notna(df), None)


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _row_payload(row: dict[str, Any], tz: str, model: type[BaseModel]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        v = _clean(v)
        if v is None:
            continue
        if k.endswith("_time"):
            v = parse_timestamp(v, tz, k)
        elif k in ("comorbidities",):
            v = [x.strip() for x in str(v).split("|") if x.strip()]
        elif k in ("susceptibility", "criteria_met", "calibration_metadata"):
            import json
            v = json.loads(v)
        out[k] = v
    return out


def _wide_to_long(df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    d = load_dictionary()
    rows, issues = [], []
    var_cols: dict[str, tuple[str, str | None]] = {}
    for c in df.columns:
        if c in WIDE_ID_COLS:
            continue
        m = HEADER_UNIT_RE.match(c)
        name, unit = (m["name"], m["unit"]) if m else (c, None)
        resolved = d.resolve(name)
        if resolved is None:
            issues.append({"row": 1, "column": c, "severity": "error",
                           "message": f"column '{c}' is not a data-dictionary variable or alias - ignored"})
            continue
        var_cols[c] = (resolved, unit)
    for i, r in enumerate(df.to_dict("records")):
        for c, (var, unit) in var_cols.items():
            v = _clean(r.get(c))
            if v is None:
                continue
            item = {k: r.get(k) for k in WIDE_ID_COLS if r.get(k) is not None}
            item.update(variable=var, unit=unit, _row=i + 2, _col=c)
            if isinstance(v, str) and v.upper() in d.missing_codes:
                item["missing_reason"] = v.upper()
            else:
                item["value"] = v
            rows.append(item)
    return rows, issues


def import_file(session: Session, who: Principal, kind: str, data: bytes, filename: str,
                site_code: str | None = None, timezone: str | None = None, dry_run: bool = False) -> ImportReport:
    if kind not in IMPORT_KINDS:
        raise svc.ValidationFailed([f"unknown import kind '{kind}' (choose from {sorted(IMPORT_KINDS)})"])
    model, create = IMPORT_KINDS[kind]
    tz = timezone
    site_id = None
    if site_code:
        site = svc.get_site(session, site_code)
        if not who.can_access_site(site.id):
            raise svc.Forbidden("cannot import for another site")
        site_id = site.id
        tz = tz or site.timezone
    df = read_table(data, filename)
    batch = ImportBatch(kind=kind, filename=filename[:255], sha256=sha256_bytes(data), site_id=site_id,
                        timezone_assumed=tz, dry_run=dry_run, imported_by=who.user_id)
    session.add(batch)
    session.flush()
    issues: list[dict[str, Any]] = []
    if kind == "measurements" and "variable" not in df.columns:
        records, wide_issues = _wide_to_long(df)
        issues += wide_issues
    else:
        records = [{**r, "_row": i + 2, "_col": None} for i, r in enumerate(df.to_dict("records"))]

    accepted = flagged = rejected = 0
    outer = session.begin_nested()            # whole import can be rolled back for dry runs
    for rec in records:
        rownum, col = rec.pop("_row"), rec.pop("_col")
        try:
            payload = _row_payload(rec, tz, model)
            if site_id and "study_id" in payload and kind != "patients":
                p = session.query(Patient).filter_by(study_id=payload["study_id"]).one_or_none()
                if p is not None and p.site_id != site_id:
                    raise svc.ValidationFailed([f"{payload['study_id']} is not registered at site {site_code}"])
            obj = model.model_validate(payload)
            with session.begin_nested():
                row = create(session, who, obj, source="import", batch_id=batch.id)
            accepted += 1
            if getattr(row, "dq_flags", None):
                flagged += 1
                issues += [{"row": rownum, "column": col, "severity": "flag", "message": f} for f in row.dq_flags]
        except ValidationError as e:
            rejected += 1
            for err in e.errors():
                issues.append({"row": rownum, "column": col or ".".join(map(str, err["loc"])), "severity": "error",
                               "message": err["msg"]})
        except (svc.ServiceError, NaiveDatetimeError, ValueError) as e:
            rejected += 1
            issues.append({"row": rownum, "column": col, "severity": "error", "message": str(e)})
    if dry_run:
        outer.rollback()
        session.add(batch)
    else:
        outer.commit()
    batch.rows_total = len(records)
    batch.rows_accepted, batch.rows_flagged, batch.rows_rejected = accepted, flagged, rejected
    batch.issues = issues
    from irtcri.db.audit import audit
    audit(session, who, "import" if not dry_run else "import_dry_run", "import_batch", batch.id,
          after={"kind": kind, "filename": filename, "sha256": batch.sha256, "accepted": accepted,
                 "rejected": rejected})
    return ImportReport(batch, issues)
