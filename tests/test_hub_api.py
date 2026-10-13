"""Data Hub API tests: auth/RBAC, site scoping, validation, corrections, audit, imports."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import T0, thermal_npy


@pytest.fixture()
def client(env):
    from irtcri.hub.app import create_app
    return TestClient(create_app())


def _bootstrap(client):
    r = client.post("/api/v1/auth/bootstrap", params={"username": "admin"},
                    headers={"X-Bootstrap-Token": "bootstrap-secret-for-tests"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def api(client):
    admin = _bootstrap(client)
    for code, tz in (("S01", "Europe/Berlin"), ("S02", "Africa/Cairo")):
        assert client.post("/api/v1/sites", json={"site_code": code, "name": code, "timezone": tz},
                           headers=admin).status_code == 201
    tokens = {"admin": admin}
    for name, role, site in (("dman", "DataManager", None), ("res1", "SiteResearcher", "S01"),
                             ("res2", "SiteResearcher", "S02"), ("ann1", "Annotator", None),
                             ("ann2", "Annotator", None), ("clin", "ClinicianReviewer", None),
                             ("aud", "ReadOnlyAuditor", None), ("mlr", "MLResearcher", None)):
        r = client.post("/api/v1/users", json={"username": name, "role": role, "site_code": site}, headers=admin)
        assert r.status_code == 201, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['token']}"}
    return tokens


def _enrol(client, h, site="S01"):
    r = client.post("/api/v1/patients", json={"site_code": site, "enrolment_date": "2026-03-02", "age_years": 70,
                                              "sex": "male"}, headers=h)
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    r = client.post("/api/v1/episodes", json={"study_id": sid, "catheter_type": "CVC",
                                              "insertion_site": "subclavian_right",
                                              "insertion_time": T0.isoformat()}, headers=h)
    assert r.status_code == 201, r.text
    return sid, r.json()["episode_code"]


def test_auth_required_and_bootstrap_once(client):
    assert client.get("/api/v1/patients").status_code == 401
    _bootstrap(client)
    r = client.post("/api/v1/auth/bootstrap", params={"username": "x"},
                    headers={"X-Bootstrap-Token": "bootstrap-secret-for-tests"})
    assert r.status_code == 409
    assert client.get("/api/v1/patients", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_role_permissions(client, api):
    # annotators cannot read clinical data or enrol
    assert client.get("/api/v1/patients", headers=api["ann1"]).status_code == 403
    assert client.post("/api/v1/patients", json={"site_code": "S01", "enrolment_date": "2026-03-02"},
                       headers=api["ann1"]).status_code == 403
    # auditor can read audit trail but not clinical data
    assert client.get("/api/v1/audit", headers=api["aud"]).status_code == 200
    assert client.get("/api/v1/patients", headers=api["aud"]).status_code == 403
    me = client.get("/api/v1/auth/me", headers=api["res1"]).json()
    assert "export_data" not in me["permissions"]


def test_site_scoping(client, api):
    sid, _ = _enrol(client, api["res1"], "S01")
    # researcher from S02 cannot enrol into S01 nor see S01 participants
    assert client.post("/api/v1/patients", json={"site_code": "S01", "enrolment_date": "2026-03-02"},
                       headers=api["res2"]).status_code == 403
    assert client.get(f"/api/v1/patients/{sid}", headers=api["res2"]).status_code == 403
    assert all(p["study_id"] != sid for p in client.get("/api/v1/patients", headers=api["res2"]).json())
    assert client.get(f"/api/v1/patients/{sid}", headers=api["dman"]).status_code == 200


def test_no_identifiers_accepted(client, api):
    r = client.post("/api/v1/patients", json={"site_code": "S01", "enrolment_date": "2026-03-02",
                                              "name": "John Doe"}, headers=api["res1"])
    assert r.status_code == 422
    r = client.post("/api/v1/patients", json={"site_code": "S01", "enrolment_date": "2026-03-02",
                                              "study_id": "MRN123456"}, headers=api["res1"])
    assert r.status_code == 422


def test_measurement_validation_units_and_flags(client, api):
    sid, ep = _enrol(client, api["res1"])
    base = {"study_id": sid, "episode_code": ep, "event_time": "2026-03-02T10:00:00+01:00"}
    # naive timestamp rejected
    r = client.post("/api/v1/measurements", json={**base, "variable": "heart_rate", "value": 80,
                                                  "event_time": "2026-03-02T10:00:00"}, headers=api["res1"])
    assert r.status_code == 422
    # impossible value rejected
    r = client.post("/api/v1/measurements", json={**base, "variable": "heart_rate", "value": 420},
                    headers=api["res1"])
    assert r.status_code == 422 and "impossible" in r.text
    # unit conversion (creatinine mg/dL -> umol/L) via alias handling
    r = client.post("/api/v1/measurements", json={**base, "variable": "creatinine", "value": 2.0, "unit": "mg/dL",
                                                  "source_time": "2026-03-02T13:00:00+01:00"}, headers=api["res1"])
    assert r.status_code == 201, r.text
    assert r.json()["table"] == "laboratory_result" and abs(r.json()["value"] - 176.84) < 0.01
    # implausible -> stored, flagged, query raised
    r = client.post("/api/v1/measurements", json={**base, "variable": "temp", "value": 42.0}, headers=api["res1"])
    assert r.status_code == 201 and r.json()["variable"] == "temperature_c" and r.json()["dq_flags"]
    qs = client.get("/api/v1/queries", headers=api["dman"]).json()
    assert any(q["field"] == "temperature_c" and q["auto_generated"] for q in qs)
    # cross-field rule
    client.post("/api/v1/measurements", json={**base, "variable": "sbp", "value": 80}, headers=api["res1"])
    r = client.post("/api/v1/measurements", json={**base, "variable": "dbp", "value": 95}, headers=api["res1"])
    assert any("sbp_gt_dbp" in f for f in r.json()["dq_flags"])
    # explicit missingness
    r = client.post("/api/v1/measurements", json={**base, "variable": "lactate", "missing_reason": "ND"},
                    headers=api["res1"])
    assert r.status_code == 201
    # lab report before collection rejected
    r = client.post("/api/v1/measurements", json={**base, "variable": "wbc", "value": 9,
                                                  "source_time": "2026-03-02T09:00:00+01:00"}, headers=api["res1"])
    assert r.status_code == 422


def test_correction_and_void_are_audited(client, api):
    sid, ep = _enrol(client, api["res1"])
    r = client.post("/api/v1/measurements", json={"study_id": sid, "variable": "heart_rate", "value": 88,
                                                  "event_time": "2026-03-02T10:00:00Z"}, headers=api["res1"])
    oid = r.json()["id"]
    r = client.patch(f"/api/v1/records/clinical_observation/{oid}",
                     json={"changes": {"value_num": 98}, "reason": "transcription error", "expected_version": 1},
                     headers=api["res1"])
    assert r.status_code == 200 and r.json()["record_version"] == 2
    # stale version -> conflict
    r = client.patch(f"/api/v1/records/clinical_observation/{oid}",
                     json={"changes": {"value_num": 99}, "reason": "again", "expected_version": 1},
                     headers=api["res1"])
    assert r.status_code == 409
    # impossible correction rejected
    r = client.patch(f"/api/v1/records/clinical_observation/{oid}",
                     json={"changes": {"value_num": 999}, "reason": "typo fix"}, headers=api["res1"])
    assert r.status_code == 422
    # immutable field
    r = client.patch(f"/api/v1/records/clinical_observation/{oid}",
                     json={"changes": {"patient_id": "x"}, "reason": "should fail"}, headers=api["res1"])
    assert r.status_code == 422
    r = client.post(f"/api/v1/records/clinical_observation/{oid}/void", json={"reason": "entered twice"},
                    headers=api["res1"])
    assert r.status_code == 200
    trail = client.get("/api/v1/audit", params={"entity_id": oid}, headers=api["aud"]).json()
    actions = [e["action"] for e in trail]
    assert {"create", "update", "void"} <= set(actions)
    upd = next(e for e in trail if e["action"] == "update")
    assert upd["before"]["value_num"] == 88 and upd["after"]["value_num"] == 98 and upd["reason"]


def test_outcome_rules(client, api):
    sid, ep = _enrol(client, api["res1"])
    r = client.post("/api/v1/outcomes", json={"study_id": sid, "episode_code": ep,
                                              "outcome_type": "exit_site_infection"}, headers=api["res1"])
    assert r.status_code == 422  # recognition time mandatory for events
    r = client.post("/api/v1/outcomes", json={"study_id": sid, "episode_code": ep,
                                              "outcome_type": "exit_site_infection",
                                              "clinical_recognition_time": "2026-03-04T09:00:00Z"},
                    headers=api["res1"])
    assert r.status_code == 201 and r.json()["is_event"] is True
    r = client.post("/api/v1/outcomes", json={"study_id": sid, "episode_code": ep, "outcome_type": "no_infection"},
                    headers=api["res1"])
    assert r.status_code == 409


def test_csv_import_dry_run_and_commit(client, api):
    sid, ep = _enrol(client, api["res1"])
    csv = ("study_id,episode_code,event_time,heart_rate,creatinine [mg/dL],temp,unknown_col\n"
           f"{sid},{ep},2026-03-02 10:00,80,1.1,37.2,x\n"
           f"{sid},{ep},2026-03-02 16:00,500,ND,38.0,y\n"
           f"NOPE-0001,{ep},2026-03-02 22:00,70,,,\n")
    files = {"file": ("vitals.csv", csv.encode(), "text/csv")}
    r = client.post("/api/v1/imports/measurements", files=files, data={"site_code": "S01", "dry_run": "true"},
                    headers=api["res1"])
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["timezone_assumed"] == "Europe/Berlin"
    assert rep["rows_accepted"] == 5 and rep["rows_rejected"] == 2
    msgs = " ".join(i["message"] for i in rep["issues"])
    assert "impossible" in msgs and "unknown study_id" in msgs and "unknown_col" in msgs
    pid = client.get(f"/api/v1/patients/{sid}", headers=api["dman"]).json()["id"]
    tl = client.get(f"/api/v1/patients/{sid}/timeline", headers=api["dman"]).json()
    assert tl["observations"] == []   # dry run wrote nothing
    r = client.post("/api/v1/imports/measurements", files=files, data={"site_code": "S01", "dry_run": "false"},
                    headers=api["res1"])
    tl = client.get(f"/api/v1/patients/{sid}/timeline", headers=api["dman"]).json()
    hr = [o for o in tl["observations"] if o["variable"] == "heart_rate"]
    assert len(hr) == 1 and hr[0]["time"].startswith("2026-03-02T09:00")  # Berlin local -> UTC
    assert pid


def test_image_upload_qc_and_access(client, api):
    sid, ep = _enrol(client, api["res1"])
    meta = {"episode_code": ep, "acquisition_time": "2026-03-02T12:00:00Z", "view": "frontal",
            "distance_cm": 40, "ambient_temperature_c": 23, "emissivity": 0.98, "device_id": "CAM1",
            "operator_id": "OP7"}
    r = client.post("/api/v1/images/thermal", files={"file": ("a.npy", thermal_npy(1.5), "application/octet-stream")},
                    data={"metadata": json.dumps(meta)}, headers=api["res1"])
    assert r.status_code == 201, r.text
    img = r.json()
    assert img["qc_status"] == "PASS", img
    # duplicate bytes at a different declared time -> FAIL, but stored (not deleted)
    r = client.post("/api/v1/images/thermal", files={"file": ("b.npy", thermal_npy(1.5), "application/octet-stream")},
                    data={"metadata": json.dumps({**meta, "acquisition_time": "2026-03-02T23:00:00Z"})},
                    headers=api["res1"])
    assert r.json()["qc_status"] == "FAIL" and any(x["check"] == "duplicate" for x in r.json()["qc_reasons"])
    # corrupted file
    r = client.post("/api/v1/images/thermal", files={"file": ("c.npy", b"garbage" * 100, "application/octet-stream")},
                    data={"metadata": json.dumps({**meta, "acquisition_time": "2026-03-03T12:00:00Z"})},
                    headers=api["res1"])
    assert r.json()["qc_status"] == "FAIL"
    imgs = client.get("/api/v1/images/thermal", params={"episode_code": ep}, headers=api["dman"]).json()
    assert len(imgs) == 3
    # render permissions: researcher ok, unassigned annotator not
    assert client.get(f"/api/v1/images/thermal/{img['id']}/render.png", headers=api["res1"]).status_code == 200
    assert client.get(f"/api/v1/images/thermal/{img['id']}/render.png", headers=api["ann1"]).status_code == 403
    # QC override keeps the automated result
    r = client.post(f"/api/v1/images/thermal/{imgs[2]['id']}/qc-override",
                    json={"status": "FAIL", "reason": "confirmed unreadable"}, headers=api["dman"])
    assert r.json()["qc_status"] == "FAIL" and r.json()["effective_qc_status"] == "FAIL"


def test_annotation_blinding_and_adjudication(client, api):
    sid, ep = _enrol(client, api["res1"])
    client.post("/api/v1/outcomes", json={"study_id": sid, "episode_code": ep, "outcome_type": "exit_site_infection",
                                          "clinical_recognition_time": "2026-03-03T09:00:00Z"}, headers=api["res1"])
    meta = {"episode_code": ep, "acquisition_time": "2026-03-02T12:00:00Z"}
    img = client.post("/api/v1/images/thermal", files={"file": ("a.npy", thermal_npy(2), "application/octet-stream")},
                      data={"metadata": json.dumps(meta)}, headers=api["res1"]).json()["id"]
    for user, role in (("ann1", "reader_1"), ("ann2", "reader_2")):
        r = client.post("/api/v1/annotation/assignments", json={
            "thermal_image_ids": [img], "annotator_username": user, "reader_role": role,
            "blinding_profile": "image_only"}, headers=api["dman"])
        assert r.status_code == 201, r.text
    # same reader twice not allowed
    r = client.post("/api/v1/annotation/assignments", json={"thermal_image_ids": [img], "annotator_username": "ann1",
                                                            "reader_role": "adjudicator"}, headers=api["dman"])
    assert r.status_code == 409
    view = client.get(f"/api/v1/annotation/tasks/{img}", headers=api["ann1"]).json()
    blob = json.dumps(view)
    assert sid not in blob and "outcome" not in view and "exit_site_infection" not in blob
    assert "acquisition_time" not in view and view["task_code"].startswith("T-")
    assert client.get(f"/api/v1/images/thermal/{img}/render.png", headers=api["ann1"]).status_code == 200
    # annotator 2 must not see reader 1's work
    client.put(f"/api/v1/annotation/tasks/{img}", json={"label": "exit_site_infection", "confidence": 4,
                                                        "submit": True, "insertion_point": {"x": 80, "y": 60}},
               headers=api["ann1"])
    v2 = client.get(f"/api/v1/annotation/tasks/{img}", headers=api["ann2"]).json()
    assert "other_annotations" not in v2
    client.put(f"/api/v1/annotation/tasks/{img}", json={"label": "no_infection", "confidence": 2, "submit": True},
               headers=api["ann2"])
    q = client.get("/api/v1/annotation/adjudication-queue", headers=api["clin"]).json()
    assert q and q[0]["thermal_image_id"] == img
    # reader cannot adjudicate; clinician reviewer can
    r = client.post("/api/v1/annotation/adjudications", json={"thermal_image_id": img,
                                                              "final_label": "exit_site_infection"},
                    headers=api["clin"])
    assert r.status_code == 201, r.text
    assert client.get("/api/v1/annotation/adjudication-queue", headers=api["clin"]).json() == []
    rep = client.get("/api/v1/annotation/agreement", params={"n_boot": 20}, headers=api["dman"]).json()
    assert rep["n_images"] == 1
    # revising a submitted annotation creates a new version, keeping the old
    r = client.put(f"/api/v1/annotation/tasks/{img}", json={"label": "possible_infection", "submit": True},
                   headers=api["ann2"])
    assert r.json()["version"] == 2 and r.json()["supersedes_id"]


def test_prediction_governance(client, api, monkeypatch):
    sid, ep = _enrol(client, api["res1"])
    mv = client.post("/api/v1/models", json={"name": "thermal_cnn", "version": "0.1.0", "family": "F1",
                                             "intended_use": "research", "limitations": "pilot",
                                             "threshold": 0.4}, headers=api["mlr"])
    assert mv.status_code == 201, mv.text
    body = {"model_name": "thermal_cnn", "model_version": "0.1.0", "episode_code": ep,
            "prediction_time": "2026-03-02T12:00:00Z", "probability": 0.7}
    r = client.post("/api/v1/predictions", json=body, headers=api["mlr"])
    assert r.status_code == 201 and r.json()["display"]["message"].startswith("Elevated model-estimated risk")
    assert "investigational" in r.json()["display"]["disclaimer"]
    r = client.post("/api/v1/predictions", json={**body, "mode": "prospective_alert"}, headers=api["mlr"])
    assert r.status_code == 403
    # ML researcher cannot lock; admin needs an approval reference
    mid = mv.json()["id"]
    assert client.post(f"/api/v1/models/{mid}/status", json={"status": "validated_locked", "reason": "validated"},
                       headers=api["mlr"]).status_code == 403
    assert client.post(f"/api/v1/models/{mid}/status", json={"status": "validated_locked", "reason": "validated"},
                       headers=api["admin"]).status_code == 422
    tl = client.get(f"/api/v1/patients/{sid}/timeline", headers=api["dman"]).json()
    assert tl["episodes"][0]["predictions"][0]["probability"] == 0.7
    tl = client.get(f"/api/v1/patients/{sid}/timeline", headers=api["res1"]).json()
    assert "predictions" not in tl["episodes"][0]


def test_dq_summary_and_site_report(client, api):
    sid, ep = _enrol(client, api["res1"])
    client.post("/api/v1/measurements", json={"study_id": sid, "variable": "heart_rate", "value": 80,
                                              "event_time": "2026-03-02T10:00:00Z"}, headers=api["res1"])
    dq = client.get("/api/v1/dq/summary", headers=api["dman"]).json()
    assert dq["recruitment"]["enrolled_total"] == 1
    assert dq["imaging"]["expected_timepoints"] > 0 and dq["imaging"]["missing_timepoints"]
    assert client.get("/api/v1/dq/summary", headers=api["res2"]).json()["recruitment"]["enrolled_total"] == 0
    r = client.get("/api/v1/dq/site-report/S01", params={"month": "2026-03"}, headers=api["dman"])
    assert r.status_code == 200 and "Site QC report" in r.text
    assert client.get(f"/api/v1/dq/patients/{sid}", headers=api["dman"]).json()["study_id"] == sid


def test_snapshot_export(client, api):
    _enrol(client, api["res1"])
    r = client.post("/api/v1/exports/snapshot", headers=api["dman"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert client.post("/api/v1/exports/snapshot", headers=api["res1"]).status_code == 403
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "manifest.json" in z.namelist() and "parquet/patient.parquet" in z.namelist()
    assert "token_hash" not in z.read("csv/patient.csv").decode()


def test_withdrawal_blocks_new_data_keeps_records_and_controls_export(client, api):
    sid, ep = _enrol(client, api["res1"])
    base = {"study_id": sid, "episode_code": ep, "variable": "heart_rate", "value": 80}
    assert client.post("/api/v1/measurements", json={**base, "event_time": "2026-03-02T10:00:00Z"},
                       headers=api["res1"]).status_code == 201
    r = client.post(f"/api/v1/patients/{sid}/withdrawal", json={
        "withdrawn_time": "2026-03-03T00:00:00Z", "reason": "participant request", "data_use_after_withdrawal": False},
        headers=api["res1"])
    assert r.status_code == 200, r.text
    # no data after withdrawal; data before withdrawal can still be completed
    assert client.post("/api/v1/measurements", json={**base, "event_time": "2026-03-04T10:00:00Z"},
                       headers=api["res1"]).status_code == 422
    assert client.post("/api/v1/measurements", json={**base, "event_time": "2026-03-02T16:00:00Z"},
                       headers=api["res1"]).status_code == 201
    # records are retained in the hub (audit trail) ...
    tl = client.get(f"/api/v1/patients/{sid}/timeline", headers=api["dman"]).json()
    assert len(tl["observations"]) == 2
    trail = client.get("/api/v1/audit", params={"entity_type": "patient"}, headers=api["aud"]).json()
    assert any(e["action"] == "withdraw" for e in trail)
    # ... but never leave it again when no further data use was consented
    import io
    import json as _json
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(client.post("/api/v1/exports/snapshot", headers=api["dman"]).content))
    assert sid not in z.read("csv/patient.csv").decode()
    assert _json.loads(z.read("manifest.json"))["participants_excluded_withdrawn_no_data_use"] == 1


def test_original_filenames_never_stored(client, api):
    sid, ep = _enrol(client, api["res1"])
    meta = {"episode_code": ep, "acquisition_time": "2026-03-02T12:00:00Z"}
    r = client.post("/api/v1/images/thermal",
                    files={"file": ("John_Doe_MRN1234567.npy", thermal_npy(1.0), "application/octet-stream")},
                    data={"metadata": json.dumps(meta)}, headers=api["res1"])
    img = client.get(f"/api/v1/images/thermal/{r.json()['id']}", headers=api["dman"]).json()
    blob = json.dumps(img)
    assert "John" not in blob and "1234567" not in blob
    assert img["original_filename"].startswith("thermal_") and img["extra_metadata"]["original_filename_sha256"]
    assert client.get(f"/api/v1/images/thermal/{img['id']}/render.png", headers=api["dman"]).status_code == 200
