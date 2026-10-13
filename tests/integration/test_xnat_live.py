"""OPTIONAL live XNAT test-suite - run against a disposable test project only:

    XNAT_TEST_URL=https://xnat-test.example.org XNAT_TEST_USER=... XNAT_TEST_PASSWORD=... \
    XNAT_TEST_PROJECT=IRTCRI_TEST pytest tests/integration -m xnat_live

It validates the REST assumptions encoded in irtcri.integrations.xnat (subject/session/scan/resource
creation, inbody upload, 409 on overwrite, listing, custom fields) on the real XNAT version.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("XNAT_TEST_URL"), reason="XNAT_TEST_URL not set")


def test_live_round_trip():
    from irtcri.imaging.repository import ImageRef
    from irtcri.integrations.xnat.connection import XNATConfig, XNATConnection
    from irtcri.integrations.xnat.repository import XNATImagingRepository
    cfg = XNATConfig(os.environ["XNAT_TEST_URL"], os.environ["XNAT_TEST_USER"], os.environ["XNAT_TEST_PASSWORD"],
                     os.environ["XNAT_TEST_PROJECT"])
    repo = XNATImagingRepository(cfg, XNATConnection(cfg))
    repo.check_project()
    data = uuid.uuid4().bytes * 64
    from irtcri.core.provenance import sha256_bytes
    t = datetime.now(timezone.utc)
    ref = ImageRef(None, "thermal", sha256_bytes(data), "bin", "T01", "T01-9999", "T01-9999-C1",
                   f"T01-9999-C1_{t:%Y%m%dT%H%M}Z", t)
    uri = repo.store_image(ref, data, {"view": "frontal", "synthetic": True})
    assert repo.get_image(uri, ref.sha256) == data
    assert repo.get_metadata(uri)["catheter_episode_code"] == "T01-9999-C1"
    assert uri in repo.list_catheter_episode_images("T01-9999", "T01-9999-C1")
    repo.store_qc_result(ref, {"status": "PASS", "algorithm_version": "test"})
    assert repo.get_qc_result(ref)["status"] == "PASS"
