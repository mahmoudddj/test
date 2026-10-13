"""Database-level guarantees, importers (xlsx), FHIR mapping, dictionary integrity."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy.exc import IntegrityError, StatementError

from conftest import T0


def test_naive_datetimes_rejected_by_db(session, seeded):
    from irtcri.db.models import ClinicalObservation
    session.add(ClinicalObservation(patient_id=seeded["patient"].id, variable="heart_rate", value_num=80,
                                    event_time=datetime(2026, 3, 2, 10, 0)))
    with pytest.raises((StatementError, ValueError)):
        session.flush()
    session.rollback()


def test_timestamps_round_trip_as_utc(session, seeded):
    from irtcri.db.models import CatheterEpisode
    ep = session.get(CatheterEpisode, seeded["episode"].id)
    session.expire_all()
    ep = session.get(CatheterEpisode, seeded["episode"].id)
    assert ep.insertion_time == T0 and ep.insertion_time.tzinfo is not None


def test_episode_check_constraint(session, seeded):
    from irtcri.db.models import CatheterEpisode
    session.add(CatheterEpisode(episode_code="X", patient_id=seeded["patient"].id, site_id=seeded["patient"].site_id,
                                catheter_type="CVC", insertion_site="other", insertion_time=T0,
                                follow_up_start_time=T0 - timedelta(hours=1)))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_multiple_episodes_per_patient(session, seeded, admin):
    from irtcri.hub import schemas as S
    from irtcri.hub import services as svc
    ep2 = svc.create_episode(session, admin, S.EpisodeIn(study_id=seeded["patient"].study_id, catheter_type="PORT",
                                                         insertion_site="chest_wall_left",
                                                         insertion_time=T0 + timedelta(days=20)))
    assert ep2.episode_code.endswith("-C2")


def test_xlsx_patient_import(session, seeded, admin):
    from irtcri.hub.importers import import_file
    df = pd.DataFrame({"enrolment_date": ["2026-03-05", "2026-03-06"], "age_years": [55, 300],
                       "sex": ["male", "female"], "diabetes": [True, False], "comorbidities": ["I10|E11", ""]})
    df["site_code"] = "S01"
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    rep = import_file(session, admin, "patients", buf.getvalue(), "baseline.xlsx", "S01", dry_run=False)
    assert rep.batch.rows_accepted == 1 and rep.batch.rows_rejected == 1
    assert "impossible" in rep.issues[0]["message"]


def test_fhir_observation_mapping(session, seeded, admin):
    from irtcri.hub import services as svc
    from irtcri.integrations.fhir import map_bundle
    sid = seeded["patient"].study_id
    obs = [
        {"resourceType": "Observation", "id": "o1", "status": "final", "subject": {"reference": "Patient/123"},
         "code": {"coding": [{"system": "http://loinc.org", "code": "2160-0"}]},
         "valueQuantity": {"value": 1.2, "unit": "mg/dL", "code": "mg/dL"},
         "effectiveDateTime": "2026-03-02T06:00:00+01:00", "issued": "2026-03-02T08:00:00+01:00"},
        {"resourceType": "Observation", "id": "o2", "status": "final", "subject": {"reference": "Patient/999"},
         "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}]}, "valueQuantity": {"value": 90},
         "effectiveDateTime": "2026-03-02T06:00:00Z"},
        {"resourceType": "Observation", "id": "o3", "status": "entered-in-error",
         "subject": {"reference": "Patient/123"},
         "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}]}, "valueQuantity": {"value": 90},
         "effectiveDateTime": "2026-03-02T06:00:00Z"},
    ]
    ms, skipped = map_bundle(obs, {"Patient/123": sid})
    assert len(ms) == 1 and skipped == 2
    row = svc.create_measurement(session, admin, ms[0])
    assert row.variable == "creatinine" and abs(row.value_num - 106.1) < 0.1 and row.source == "ehr"
    assert row.source_time == datetime(2026, 3, 2, 7, 0, tzinfo=timezone.utc)


def test_dictionary_integrity():
    from irtcri.schemas.dictionary import dictionary_markdown, load_dictionary
    d = load_dictionary()
    names = [v.name for v in d.variables]
    assert len(names) == len(set(names))
    for v in d.variables:
        assert v.label and v.source and v.time_reference
        if v.type in ("float", "int"):
            assert v.unit and v.hard_range, v.name
            if v.plausible_range:
                assert v.hard_range[0] <= v.plausible_range[0] <= v.plausible_range[1] <= v.hard_range[1], v.name
        if v.type == "category":
            assert v.allowed_values
    required_protocol_vars = {"temperature_c", "heart_rate", "spo2", "sbp", "dbp", "map", "respiratory_rate", "hco3",
                              "fio2", "paco2", "sao2", "ast", "bun", "urea", "calcium", "creatinine", "bilirubin",
                              "glucose", "lactate", "magnesium", "phosphate", "troponin_i", "hematocrit",
                              "haemoglobin", "ptt", "wbc", "platelets", "apache_ii"}
    assert required_protocol_vars <= set(names)
    assert "| `creatinine` |" in dictionary_markdown(d)


def test_audit_is_written_for_every_create(session, seeded):
    from sqlalchemy import select

    from irtcri.db.models import AuditEvent
    ev = session.scalars(select(AuditEvent)).all()
    assert {e.entity_type for e in ev} >= {"site", "patient", "catheter_episode"}


def test_data_dictionary_csv_is_in_sync_with_yaml():
    """docs/DATA_DICTIONARY.csv is generated (`irtcri dictionary-docs`); regenerate after editing the YAML."""
    from pathlib import Path

    from irtcri.schemas.dictionary import dictionary_csv
    assert Path("docs/DATA_DICTIONARY.csv").read_text() == dictionary_csv()
