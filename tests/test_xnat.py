"""XNAT integration tests against a mocked XNAT (no server needed). The platform must work with XNAT
disabled (all other test modules) and, when enabled, keep originals byte-identical, never overwrite
them, verify checksums, keep the catheter episode in metadata, and never let XNAT weaken leakage
safeguards."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from conftest import T0, rgb_jpeg, thermal_npy
from fake_xnat import FakeXNAT
from irtcri.core.provenance import sha256_bytes

META = {"view": "frontal", "distance_cm": 40.0, "ambient_temperature_c": 22.5, "emissivity": 0.98,
        "device_id": "S01-CAM1", "operator_id": "OP1", "camera_model": "C5"}


@pytest.fixture()
def xnat(env, monkeypatch):
    fake = FakeXNAT()
    for k, v in {"XNAT_ENABLED": "true", "XNAT_URL": "https://xnat.test", "XNAT_USERNAME": "svc",
                 "XNAT_PASSWORD": "pw", "XNAT_PROJECT_ID": "IRTCRI"}.items():
        monkeypatch.setenv(k, v)
    from irtcri.core.config import reset_settings_cache
    from irtcri.imaging.repository import set_xnat_transport
    reset_settings_cache()
    set_xnat_transport(fake.transport())
    yield fake
    set_xnat_transport(None)
    reset_settings_cache()


def _ingest(session, admin, seeded, data=None, modality="thermal", h=3, fname="a.npy"):
    from irtcri.imaging.ingest import ingest_image
    return ingest_image(session, admin, modality, data or thermal_npy(1.0, seed=h), fname,
                        {**META, "episode_id": seeded["episode"].id, "acquisition_time": T0 + timedelta(hours=h)})


# ------------------------------------------------------------------------- client
def test_client_auth_and_project_lookup(xnat):
    from irtcri.integrations.xnat.connection import XNATConfig, XNATConnection, XNATError
    from irtcri.integrations.xnat.projects import get_project
    cfg = XNATConfig("https://xnat.test", "svc", "pw", "IRTCRI")
    conn = XNATConnection(cfg, xnat.transport())
    assert get_project(conn, "IRTCRI")["items"][0]["data_fields"]["ID"] == "IRTCRI"
    assert xnat.sessions_issued == 1                       # JSESSION opened once and reused
    with pytest.raises(XNATError) as e:
        get_project(conn, "NOPE")
    assert e.value.status == 404 and "XNAT admin" in str(e.value)
    bad = XNATConnection(XNATConfig("https://xnat.test", "svc", "wrong", "IRTCRI"), xnat.transport())
    with pytest.raises(XNATError) as e:
        get_project(bad, "IRTCRI")
    assert e.value.status == 401 and "wrong" not in str(e.value) and "wrong" not in repr(bad.cfg)


def test_config_requires_all_xnat_variables(monkeypatch, env):
    from irtcri.core.config import Settings
    monkeypatch.setenv("XNAT_ENABLED", "true")
    with pytest.raises(ValueError):
        Settings()


def test_mapping_rules():
    from irtcri.integrations.xnat.mapping import XNATLocation, acquisition_label, scan_id, subject_label
    assert subject_label("S01-0001") == "S01-0001"
    with pytest.raises(ValueError):
        subject_label("John_Doe")                       # never a name/MRN as XNAT subject
    assert acquisition_label("S01-0001-C1", T0) == "S01-0001-C1_20260302T0800Z"
    loc = XNATLocation("P", "S01-0001", "S01-0001-C1_20260302T0800Z", "RAW", "f.npy", scan_id("thermal", "ab" * 32))
    assert XNATLocation.parse(loc.uri) == loc
    assert loc.rest_path.startswith("/data/projects/P/subjects/S01-0001/experiments/")


# ------------------------------------------------------------------ ingestion
def test_ingest_dual_writes_original_and_metadata(session, seeded, admin, xnat):
    data = thermal_npy(1.0)
    res = _ingest(session, admin, seeded, data)
    row = res.image
    assert row.xnat_uri and row.xnat_uri.startswith("xnat://IRTCRI/S01-0001/S01-0001-C1_")
    assert row.raw_object_key and row.acquisition_id == row.xnat_uri.split("/")[4]
    # byte-identical original in RAW; companions (temperature matrix, preview) alongside
    raw = [v for k, v in xnat.files.items() if k[-2] == "RAW"]
    assert len(raw) == 1 and sha256_bytes(raw[0]) == row.raw_sha256
    assert any(n.endswith("_temperature_degC.npy") for n in xnat.file_names("THERMAL_MATRIX"))
    assert any(n.endswith("_preview.png") for n in xnat.file_names("PREVIEW"))
    # catheter episode kept as explicit metadata (session field + METADATA JSON), not as an XNAT level
    exp = next(iter(xnat.experiments.values()))
    assert exp["fields"]["episode_code"] == seeded["episode"].episode_code and exp["fields"]["site_code"] == "S01"
    doc = json.loads(next(v for k, v in xnat.files.items() if k[-2] == "METADATA"))
    assert doc["catheter_episode_code"] == seeded["episode"].episode_code and doc["raw_sha256"] == row.raw_sha256
    assert doc["metadata"]["camera_model"] == "C5"
    # paired RGB captured 5 s later joins the SAME XNAT session (acquisition)
    from irtcri.imaging.ingest import ingest_image
    rgb = ingest_image(session, admin, "rgb", rgb_jpeg(), "b.jpg", {
        **META, "episode_id": seeded["episode"].id, "acquisition_time": T0 + timedelta(hours=3, seconds=5)}).image
    assert rgb.acquisition_id == row.acquisition_id and "/resources/RGB/" in rgb.xnat_uri
    from sqlalchemy import select

    from irtcri.db.models import ExternalResource
    kinds = {e.kind for e in session.scalars(select(ExternalResource))}
    assert {"raw", "thermal_matrix", "preview"} <= kinds


def test_retrieval_from_xnat_and_checksum_verification(session, seeded, admin, xnat, monkeypatch):
    from irtcri.imaging.ingest import load_temperature, raw_bytes
    row = _ingest(session, admin, seeded).image
    monkeypatch.setenv("IRTCRI_IMAGING_PRIMARY", "xnat")
    from irtcri.core.config import reset_settings_cache
    reset_settings_cache()
    n_get = sum(1 for m, p in xnat.calls if m == "GET" and "/resources/RAW/files/" in p)
    assert sha256_bytes(raw_bytes(row)) == row.raw_sha256
    assert sum(1 for m, p in xnat.calls if m == "GET" and "/resources/RAW/files/" in p) == n_get + 1
    assert load_temperature(row).temperature_c.shape == (120, 160)
    # corrupted copy in XNAT -> checksum failure -> automatic fallback to the object store
    key = next(k for k in xnat.files if k[-2] == "RAW")
    xnat.files[key] = b"tampered"
    assert sha256_bytes(raw_bytes(row)) == row.raw_sha256


def test_originals_are_never_overwritten(xnat):
    from irtcri.integrations.xnat.connection import XNATConfig, XNATConnection
    from irtcri.integrations.xnat.upload import XNATImmutableError, upload_file
    conn = XNATConnection(XNATConfig("https://xnat.test", "svc", "pw", "IRTCRI"), xnat.transport())
    conn.request("PUT", "/data/projects/IRTCRI/subjects/S01-0001")
    conn.request("PUT", "/data/projects/IRTCRI/subjects/S01-0001/experiments/E1", params={"xsiType": "x"})
    parent = "/data/projects/IRTCRI/subjects/S01-0001/experiments/E1"
    conn.request("PUT", f"{parent}/resources/RAW")
    upload_file(conn, parent, "RAW", "a.npy", b"original")
    upload_file(conn, parent, "RAW", "a.npy", b"original")          # idempotent
    with pytest.raises(XNATImmutableError):
        upload_file(conn, parent, "RAW", "a.npy", b"different")


def test_xnat_outage_does_not_lose_images(session, seeded, admin, xnat):
    xnat.projects.clear()                                  # XNAT misconfigured / unavailable
    row = _ingest(session, admin, seeded).image
    assert row.xnat_uri is None and row.raw_object_key and "xnat_pending" in row.extra_metadata
    from irtcri.imaging.ingest import raw_bytes
    assert sha256_bytes(raw_bytes(row)) == row.raw_sha256


# --------------------------------------------------------------- QC write-back
def test_qc_worker_publishes_qc_documents(session, seeded, admin, xnat):
    from irtcri.imaging.qc_worker import run_qc_worker
    _ingest(session, admin, seeded, h=3)
    _ingest(session, admin, seeded, data=b"corrupt" * 100, h=15)
    session.commit()
    summary = run_qc_worker(session, admin)
    assert summary["processed"] == 2 and summary["published_xnat"] == 2 and summary["status"]["FAIL"] == 1
    names = xnat.file_names("QC")
    assert sum(n.endswith("_qc.json") for n in names) == 2 and any(n.endswith("_qc.png") for n in names)
    doc = json.loads(next(v for k, v in xnat.files.items() if k[-1].endswith("_qc.json")))
    for f in ("algorithm_version", "timestamp", "input_checksum", "thresholds", "output_checksum",
              "software_version", "status"):
        assert f in doc
    again = run_qc_worker(session, admin)                  # up to date -> nothing re-processed
    assert again["processed"] == 0 and again["skipped_up_to_date"] == 2


# ------------------------------------------------------------------ migration
def test_migration_dry_run_then_copy_with_verification(session, seeded, admin, xnat, monkeypatch):
    # images ingested while XNAT was disabled
    monkeypatch.setenv("XNAT_ENABLED", "false")
    from irtcri.core.config import reset_settings_cache
    reset_settings_cache()
    rows = [_ingest(session, admin, seeded, h=h).image for h in (3, 15, 27)]
    session.commit()
    assert all(r.xnat_uri is None for r in rows)
    monkeypatch.setenv("XNAT_ENABLED", "true")
    reset_settings_cache()
    from irtcri.integrations.xnat.sync import migrate_images_to_xnat
    plan = migrate_images_to_xnat(session, admin, dry_run=True)
    assert len(plan["planned"]) == 3 and plan["migrated"] == 0 and not xnat.files
    assert {p["episode_code"] for p in plan["planned"]} == {seeded["episode"].episode_code}
    rep = migrate_images_to_xnat(session, admin, dry_run=False)
    assert rep["migrated"] == rep["verified"] == 3 and not rep["failed"]
    session.expire_all()
    from irtcri.imaging.storage import get_object_store, raw_bucket
    for r in rows:
        r = session.get(type(r), r.id)
        assert r.xnat_uri and get_object_store().exists(raw_bucket(), r.raw_object_key)   # source retained
    assert migrate_images_to_xnat(session, admin, dry_run=True)["planned"] == []           # idempotent


def test_pull_registers_site_uploaded_xnat_images(session, seeded, admin, xnat):
    """XNAT-first workflow: a site uploads to XNAT (session fields set); the hub registers + QCs it."""
    from irtcri.integrations.xnat.connection import XNATConfig, XNATConnection
    from irtcri.integrations.xnat.sync import pull_from_xnat
    conn = XNATConnection(XNATConfig("https://xnat.test", "svc", "pw", "IRTCRI"), xnat.transport())
    sid, ep = seeded["patient"].study_id, seeded["episode"].episode_code
    base = f"/data/projects/IRTCRI/subjects/{sid}"
    conn.request("PUT", base)
    xsi = "xnat:otherDicomSessionData"
    conn.request("PUT", f"{base}/experiments/SITEUPLOAD1", params={
        "xsiType": xsi, f"{xsi}/fields/field[name=episode_code]/field": ep,
        f"{xsi}/fields/field[name=acquisition_time_utc]/field": "2026-03-02T20:00:00Z"})
    conn.request("PUT", f"{base}/experiments/SITEUPLOAD2", params={"xsiType": xsi})      # no metadata
    for e in ("SITEUPLOAD1", "SITEUPLOAD2"):
        conn.request("PUT", f"{base}/experiments/{e}/scans/1", params={"xsiType": "x"})
        conn.request("PUT", f"{base}/experiments/{e}/scans/1/resources/RAW")
        conn.request("PUT", f"{base}/experiments/{e}/scans/1/resources/RAW/files/cam_0001.npy",
                     params={"inbody": "true"}, content=thermal_npy(0.8, seed=9))
    dry = pull_from_xnat(session, admin, dry_run=True)
    assert len(dry["new"]) == 1 and len(dry["errors"]) == 1 and "nothing is guessed" in dry["errors"][0]["error"]
    rep = pull_from_xnat(session, admin, dry_run=False)
    assert rep["registered"] == 1
    from sqlalchemy import select

    from irtcri.db.models import ThermalImage
    row = session.scalar(select(ThermalImage).where(ThermalImage.ingestion_channel == "xnat"))
    assert row.episode_id == seeded["episode"].id
    # site uploaded without camera/ambient metadata -> QC flags it rather than silently passing
    assert row.qc_status == "WARNING" and any(r["check"] == "missing_metadata" for r in row.qc_reasons)
    assert row.xnat_uri.endswith("/resources/RAW/files/cam_0001.npy") and row.original_filename.startswith("thermal_")
    assert pull_from_xnat(session, admin, dry_run=True)["skipped_known"] >= 1


def test_xnat_disabled_by_default(session, seeded, admin):
    from irtcri.imaging.repository import get_imaging_service
    assert not get_imaging_service().xnat_enabled
    row = _ingest(session, admin, seeded).image
    assert row.xnat_uri is None and row.acquisition_id


def test_dataset_builder_reads_images_from_xnat_with_safeguards(session, admin, xnat, monkeypatch, env):
    """Originals removed from the local store: the builder must obtain them from XNAT (checksum-verified)
    and all leakage/split safeguards must still hold."""
    import shutil

    import pandas as pd

    from irtcri.core.config import get_settings, reset_settings_cache
    from irtcri.pipeline.builder import build_dataset, load_spec
    from irtcri.synthetic import SynthConfig, generate
    generate(session, SynthConfig(n_patients=6, seed=2, rgb=False, event_rate=0.5, image_shape=(60, 80),
                                  vitals_interval_h=12))
    shutil.rmtree(get_settings().local_store_root / get_settings().s3_bucket_raw)
    monkeypatch.setenv("IRTCRI_IMAGING_PRIMARY", "xnat")
    monkeypatch.setenv("IRTCRI_DATA_DIR", str(env["tmp"] / "data"))
    reset_settings_cache()
    spec = load_spec("configs/datasets/primary_early_warning.yaml")
    spec["images"]["derive_thermal"]["size"] = 32
    m = build_dataset(session, spec)
    s = pd.read_parquet(m["root"] + "/ml/samples.parquet")
    assert m["imaging_sources"]["images_in_xnat"] == len(s) > 0
    assert s["xnat_uri"].str.startswith("xnat://IRTCRI/").all() and s["thermal_npy_key"].notna().all()
    assert s.groupby("patient_id")["split"].nunique().max() == 1
    assert (s["hours_to_recognition"].dropna() >= 0).all()


def test_prediction_and_gradcam_write_back(session, seeded, admin, xnat):
    import numpy as np

    from irtcri.db.models import ModelPrediction, ModelVersion
    from irtcri.imaging.publish import publish_annotations, publish_prediction
    row = _ingest(session, admin, seeded).image
    mv = ModelVersion(name="f1", version="r1", family="F1", threshold=0.4, training_dataset_version="ds@0.1",
                      code_version="abc123")
    session.add(mv)
    session.flush()
    pred = ModelPrediction(model_version_id=mv.id, patient_id=row.patient_id, episode_id=row.episode_id,
                           thermal_image_id=row.id, prediction_time=row.acquisition_time,
                           mode="retrospective_replay", probability=0.7, threshold=0.4, predicted_positive=True)
    session.add(pred)
    session.flush()
    uris = publish_prediction(session, pred, mv, cam=np.random.default_rng(0).random((64, 64)).astype(np.float32))
    assert len(uris) == 2
    doc = json.loads(next(v for k, v in xnat.files.items() if k[-1].endswith("_prediction.json")))
    for f in ("model_id", "model_version", "dataset_version", "prediction_timestamp", "predicted_probability",
              "operating_threshold", "git_commit"):
        assert doc[f] is not None
    assert "MLflow" in doc["model_registry"] and any(n.endswith("_gradcam.png") for n in xnat.file_names("DERIVED"))
    assert publish_annotations(session) == 0           # nothing submitted yet -> nothing published
