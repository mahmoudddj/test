"""Image readers, QC rules, storage immutability, derivation provenance, registration."""

from __future__ import annotations

import io
from datetime import timedelta

import numpy as np
import pytest

from conftest import T0, rgb_jpeg, thermal_npy
from irtcri.imaging.qc import QCContext, run_qc
from irtcri.imaging.readers import ImageReadError, read_rgb, read_thermal
from irtcri.imaging.registration import estimate_affine
from irtcri.imaging.storage import ImmutableObjectError, LocalObjectStore

META = {"study_id": "S01-0001", "episode_id": "e", "acquisition_time": T0 + timedelta(hours=3), "device_id": "C1",
        "operator_id": "O1", "distance_cm": 40, "view": "frontal", "ambient_temperature_c": 22.0, "emissivity": 0.98}


def ctx(**kw):
    return QCContext(now=T0 + timedelta(days=1), insertion_time=T0, **kw)


def qc(modality, data, name, meta=META, c=None):
    try:
        dec, err = (read_thermal(data, name) if modality == "thermal" else read_rgb(data, name)), None
    except ImageReadError as e:
        dec, err = None, str(e)
    return run_qc(modality, dec, err, meta, c or ctx(), name.rsplit(".", 1)[-1], len(data))


def test_good_thermal_passes():
    r = qc("thermal", thermal_npy(1.0), "a.npy")
    assert r.status == "PASS", r.reasons
    assert 30 < r.metrics["t_max"] < 40 and "dhash" in r.metrics


def test_csv_export_reader():
    t = np.full((70, 80), 33.0)
    t[30:40, 30:40] = 35.5
    csv = "Frame 1\nTemperature;degC\n" + "\n".join(";".join(f"{v:.2f}".replace(".", ",") for v in row) for row in t)
    d = read_thermal(csv.encode(), "export.csv")
    assert d.is_radiometric and d.temperature_c.shape == (70, 80) and d.temperature_c.max() == pytest.approx(35.5)


def test_kelvin_units_fail_and_extremes_warn():
    t = np.load(io.BytesIO(thermal_npy())) + 273.15
    b = io.BytesIO()
    np.save(b, t)
    r = qc("thermal", b.getvalue(), "k.npy")
    assert r.status == "FAIL" and any(f.check == "thermal_range" for f in r.findings)
    flat = io.BytesIO()
    np.save(flat, np.full((120, 160), 24.0, dtype=np.float32))
    r = qc("thermal", flat.getvalue(), "flat.npy")
    assert r.status == "WARNING" and {"extreme_temperature", "abnormal_statistics"} <= {f.check for f in r.findings}


def test_corrupt_wrong_format_small_and_missing_metadata():
    assert qc("thermal", thermal_npy()[:300], "x.npy").status == "FAIL"
    r = qc("thermal", thermal_npy(), "x.bmp")
    assert any(f.check == "file_format" for f in r.findings)
    m = {k: v for k, v in META.items() if k not in ("device_id", "ambient_temperature_c")}
    r = qc("thermal", thermal_npy(), "x.npy", m)
    assert r.status == "WARNING" and sum(f.check == "missing_metadata" for f in r.findings) == 2
    r = qc("thermal", thermal_npy(), "x.npy", {**META, "acquisition_time": None})
    assert r.status == "FAIL"


def test_timestamp_rules():
    r = qc("thermal", thermal_npy(), "x.npy", {**META, "acquisition_time": T0 - timedelta(hours=1)})
    assert r.status == "FAIL"
    r = qc("thermal", thermal_npy(), "x.npy", {**META, "acquisition_time": T0 + timedelta(days=3)})
    assert r.status == "FAIL"   # future relative to ctx.now
    c = ctx(removal_time=T0 + timedelta(minutes=30))   # image at +3 h is beyond the 2 h grace
    assert qc("thermal", thermal_npy(), "x.npy", c=c).status == "FAIL"


def test_duplicates():
    r1 = qc("thermal", thermal_npy(seed=1), "x.npy")
    c = ctx(exact_duplicate_ids=["img1"])
    assert qc("thermal", thermal_npy(seed=1), "x.npy", c=c).status == "FAIL"
    c = ctx(episode_images=[("img2", META["acquisition_time"] + timedelta(hours=1), "oblique_left",
                             r1.metrics["dhash"])])
    r = qc("thermal", thermal_npy(seed=2), "x.npy", c=c)
    assert any(f.check == "near_duplicate" for f in r.findings)


def test_rgb_blur():
    assert qc("rgb", rgb_jpeg(), "a.jpg").status == "PASS"
    r = qc("rgb", rgb_jpeg(blur=True), "a.jpg")
    assert any(f.check == "blur" for f in r.findings)


def test_configurable_thresholds():
    from irtcri.imaging.qc import load_qc_config
    cfg = {**load_qc_config(), "version": "custom"}
    cfg["thermal"] = {**cfg["thermal"], "skin_max_plausible_c": [36.0, 43.0]}
    dec = read_thermal(thermal_npy(), "a.npy")
    r = run_qc("thermal", dec, None, META, ctx(), "npy", 5000, cfg)
    assert r.config_version == "custom" and any(f.check == "extreme_temperature" for f in r.findings)


def test_store_is_write_once(tmp_path):
    s = LocalObjectStore(tmp_path)
    s.put_immutable("raw", "a/b.npy", b"123")
    s.put_immutable("raw", "a/b.npy", b"123")   # idempotent
    with pytest.raises(ImmutableObjectError):
        s.put_immutable("raw", "a/b.npy", b"456")
    with pytest.raises(ValueError):
        s.put("raw", "../escape", b"x")


def test_affine_registration():
    src = [[0, 0], [100, 0], [0, 100], [100, 100]]
    dst = [[5 + 0.5 * x, 3 + 0.5 * y] for x, y in src]
    M, rms = estimate_affine(src, dst)
    assert rms < 1e-9 and M[0][0] == pytest.approx(0.5) and M[0][2] == pytest.approx(5)


def test_ingest_derive_and_watch_folder(session, seeded, admin, tmp_path):
    import json

    from irtcri.imaging.derive import ThermalPreprocessConfig, derive_thermal, load_derived
    from irtcri.imaging.ingest import ingest_image
    from irtcri.imaging.storage import get_object_store, raw_bucket
    from irtcri.imaging.watch import scan_folder
    ep = seeded["episode"]
    res = ingest_image(session, admin, "thermal", thermal_npy(1.0), "a.npy",
                       {**META, "study_id": seeded["patient"].study_id, "episode_id": ep.id})
    raw_before = get_object_store().get(raw_bucket(), res.image.raw_object_key)
    cfg = ThermalPreprocessConfig(size=64)
    d = derive_thermal(session, res.image, cfg)
    arr = load_derived(d.object_key, d.sha256)
    assert arr.shape == (64, 64) and 0 <= arr.min() and arr.max() <= 1
    assert d.parent_sha256 == res.image.raw_sha256 and d.preprocessing_version == cfg.version
    assert derive_thermal(session, res.image, cfg).id == d.id               # cached, not recomputed
    assert get_object_store().get(raw_bucket(), res.image.raw_object_key) == raw_before   # raw untouched
    # watch folder: sidecar JSON + naming convention + bad file
    folder = tmp_path / "drop"
    folder.mkdir()
    (folder / "x1.npy").write_bytes(thermal_npy(0.5, seed=3))
    (folder / "x1.npy.json").write_text(json.dumps({"episode_id": ep.episode_code, "modality": "thermal",
                                                    "acquisition_time": "2026-03-02 21:00", "device_id": "C1"}))
    stamp = "20260303T090000"
    (folder / f"{seeded['patient'].study_id}__{ep.episode_code}__{stamp}__frontal__thermal.npy").write_bytes(
        thermal_npy(0.4, seed=4))
    (folder / "random.npy").write_bytes(thermal_npy(seed=5))
    s1 = scan_folder(session, admin, folder)
    assert s1["ingested"] == 2 and s1["errors"] == 1
    s2 = scan_folder(session, admin, folder)
    assert s2["ingested"] == 0 and s2["skipped"] == 2


def test_s3_object_store_write_once():
    moto = pytest.importorskip("moto")
    import boto3

    from irtcri.core.config import Settings
    from irtcri.imaging.storage import S3ObjectStore
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="raw")
        st = S3ObjectStore(Settings(object_store="s3", s3_region="us-east-1", s3_access_key="k", s3_secret_key="s"))
        st.put_immutable("raw", "a/1.npy", b"abc")
        assert st.exists("raw", "a/1.npy") and st.get("raw", "a/1.npy") == b"abc"
        st.put_immutable("raw", "a/1.npy", b"abc")
        with pytest.raises(ImmutableObjectError):
            st.put_immutable("raw", "a/1.npy", b"xyz")
