"""OPTIONAL live XNAT validation - runs ONLY against a designated, disposable TEST project.

Required environment variables:
    XNAT_TEST_URL           e.g. https://xnat-test.example.org
    XNAT_TEST_USERNAME      service/test account (XNAT_TEST_USER also accepted)
    XNAT_TEST_PASSWORD
    XNAT_TEST_PROJECT       an existing XNAT project ID reserved for testing (e.g. IRTCRI_TEST)
    XNAT_TEST_CONFIRM       must be exactly "test-project-only" (safety interlock)
Optional:
    XNAT_TEST_VERIFY_TLS    "false" for a self-signed test server (default true)
    XNAT_TEST_CLEANUP       "yes" to delete the synthetic test subject afterwards

Run:
    scripts/run_xnat_live_test.sh          (checks the variables, then runs pytest)
    pytest tests/integration/test_xnat_live.py -v -m xnat_live

Only a synthetic subject (label T99-<random>) is created; no clinical data are read or written.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import pytest

from irtcri.core.provenance import sha256_bytes

URL = os.environ.get("XNAT_TEST_URL")
USER = os.environ.get("XNAT_TEST_USERNAME") or os.environ.get("XNAT_TEST_USER")
PASSWORD = os.environ.get("XNAT_TEST_PASSWORD")
PROJECT = os.environ.get("XNAT_TEST_PROJECT")
CONFIRMED = os.environ.get("XNAT_TEST_CONFIRM") == "test-project-only"

pytestmark = [
    pytest.mark.xnat_live,
    pytest.mark.skipif(not (URL and USER and PASSWORD and PROJECT),
                       reason="XNAT_TEST_URL / XNAT_TEST_USERNAME / XNAT_TEST_PASSWORD / XNAT_TEST_PROJECT not set"),
]


@pytest.fixture(scope="module")
def live():
    if not CONFIRMED:
        pytest.fail('Set XNAT_TEST_CONFIRM="test-project-only" to confirm that '
                    'XNAT_TEST_PROJECT is a disposable test project')
    if PROJECT == os.environ.get("XNAT_PROJECT_ID"):
        pytest.fail("XNAT_TEST_PROJECT equals the production XNAT_PROJECT_ID - refusing to run")
    from irtcri.imaging.repository import ImageRef
    from irtcri.integrations.xnat.connection import XNATConfig, XNATConnection
    from irtcri.integrations.xnat.mapping import acquisition_label
    from irtcri.integrations.xnat.repository import XNATImagingRepository
    cfg = XNATConfig(URL.rstrip("/"), USER, PASSWORD, PROJECT,
                     verify_tls=os.environ.get("XNAT_TEST_VERIFY_TLS", "true").lower() != "false")
    conn = XNATConnection(cfg)
    repo = XNATImagingRepository(cfg, conn)
    study_id = f"T99-{uuid.uuid4().int % 100000:05d}"            # synthetic, pattern-valid Study ID
    t = datetime.now(timezone.utc).replace(microsecond=0)
    data = b"SYNTHETIC-IRTCRI-LIVE-TEST" + uuid.uuid4().bytes * 128
    ref = ImageRef(None, "thermal", sha256_bytes(data), "bin", "T99", study_id, f"{study_id}-C1",
                   acquisition_label(f"{study_id}-C1", t), t)
    yield {"cfg": cfg, "conn": conn, "repo": repo, "ref": ref, "data": data, "state": {}}
    if os.environ.get("XNAT_TEST_CLEANUP") == "yes":
        conn.request("DELETE", f"/data/projects/{PROJECT}/subjects/{study_id}", params={"removeFiles": "true"},
                     ok=(404,))
    conn.close()


def test_01_authentication(live):
    from irtcri.integrations.xnat.connection import XNATConfig, XNATConnection, XNATError
    live["conn"].login()
    assert live["conn"]._jsession
    bad = XNATConnection(XNATConfig(live["cfg"].url, USER, PASSWORD + "-wrong", PROJECT,
                                    verify_tls=live["cfg"].verify_tls))
    with pytest.raises(XNATError):
        bad.login()


def test_02_project_lookup(live):
    info = live["repo"].check_project()
    assert info                                   # 404/403 would have raised with an explanatory message


def test_03_subject_mapping_and_upload(live):
    repo, ref = live["repo"], live["ref"]
    uri = repo.store_image(ref, live["data"], {"view": "frontal", "synthetic": True, "camera_model": "LIVE-TEST"})
    live["state"]["uri"] = uri
    assert uri.startswith(f"xnat://{PROJECT}/{ref.study_id}/{ref.acquisition_id}/scans/")
    from irtcri.integrations.xnat.subjects import list_subjects
    assert ref.study_id in list_subjects(live["conn"], PROJECT)


def test_04_retrieval_is_byte_identical(live):
    assert live["repo"].get_image(live["state"]["uri"], live["ref"].sha256) == live["data"]


def test_05_original_cannot_be_overwritten(live):
    from irtcri.integrations.xnat.mapping import XNATLocation
    from irtcri.integrations.xnat.upload import XNATImmutableError, upload_file
    loc = XNATLocation.parse(live["state"]["uri"])
    upload_file(live["conn"], loc.parent_path, loc.resource, loc.name, live["data"])   # identical: accepted
    with pytest.raises(XNATImmutableError):
        upload_file(live["conn"], loc.parent_path, loc.resource, loc.name, b"different bytes")


def test_06_metadata_round_trip_and_session_fields(live):
    ref = live["ref"]
    doc = live["repo"].get_metadata(live["state"]["uri"])
    assert doc["catheter_episode_code"] == ref.episode_code and doc["raw_sha256"] == ref.sha256
    assert doc["metadata"]["camera_model"] == "LIVE-TEST"
    from irtcri.integrations.xnat.sync import _session_field_values
    fields = _session_field_values(live["conn"], PROJECT, ref.study_id, ref.acquisition_id)
    assert fields.get("episode_code") == ref.episode_code, f"custom fields not readable as expected: {fields}"
    assert live["state"]["uri"] in live["repo"].list_catheter_episode_images(ref.study_id, ref.episode_code)


def test_07_qc_and_derived_write_back(live):
    repo, ref = live["repo"], live["ref"]
    qc = {"schema": "irtcri.qc/1.0", "status": "PASS", "algorithm_version": "live-test",
          "input_checksum": ref.sha256}
    repo.store_qc_result(ref, qc, b"\x89PNG\r\n\x1a\nlive-test")
    assert repo.get_qc_result(ref)["status"] == "PASS"
    qc["status"] = "WARNING"                        # QC documents may be updated (overwrite=true)
    repo.store_qc_result(ref, qc)
    assert repo.get_qc_result(ref)["status"] == "WARNING"
    pred = json.dumps({"predicted_probability": 0.5, "synthetic": True}).encode()
    uri = repo.store_derived_resource(ref, "prediction", "live_test_prediction.json", pred, "application/json")
    assert repo.get_derived_resource(uri) == pred
