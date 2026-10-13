"""End-to-end: synthetic data -> dataset builder -> training (F0 + torch smoke) -> locked test -> replay.

Also acts as a POSITIVE CONTROL: the synthetic data contain a pre-clinical thermal signal, so an
interpretable thermal-feature baseline must achieve clearly better-than-chance validation AUROC.
If this fails, labels/features/splits are probably misaligned.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from irtcri.ml.config import ExperimentCfg


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    import os
    tmp = tmp_path_factory.mktemp("pipe")
    mp = pytest.MonkeyPatch()
    mp.setenv("IRTCRI_DATABASE_URL", f"sqlite:///{tmp}/p.db")
    mp.setenv("IRTCRI_LOCAL_STORE_ROOT", str(tmp / "obj"))
    mp.setenv("IRTCRI_DATA_DIR", str(tmp / "data"))
    from irtcri.core.config import reset_settings_cache
    from irtcri.imaging.storage import set_object_store
    reset_settings_cache()
    set_object_store(None)
    from irtcri.db.session import create_all, session_factory
    create_all()
    s = session_factory()()
    from irtcri.synthetic import SynthConfig, generate
    stats = generate(s, SynthConfig(n_patients=30, seed=3, rgb=True, event_rate=0.4,
                                    image_shape=(60, 80), vitals_interval_h=12))
    from irtcri.pipeline.builder import build_dataset, load_spec
    spec = load_spec("configs/datasets/primary_early_warning.yaml")
    spec["images"]["derive_thermal"]["size"] = 64
    spec["images"]["derive_rgb"]["size"] = 64
    spec["version"] = "test-0"
    m = build_dataset(s, spec)
    yield {"session": s, "manifest": m, "tmp": tmp, "stats": stats}
    s.close()
    set_object_store(None)
    mp.undo()
    reset_settings_cache()
    _ = os


def test_manifest_and_layers(built):
    m = built["manifest"]
    from pathlib import Path
    root = Path(m["root"])
    for p in ("raw/patient.parquet", "validated/measurements.parquet", "longitudinal/time_to_event.csv",
              "ml/samples.parquet", "ml/samples.csv", "ml/cohort_flow.md", "ml/missingness.csv", "ml/feature_spec.json",
              "ml/splits.json", "manifest.json"):
        assert (root / p).exists(), p
    for k in ("version", "created_time", "code", "included_sites", "counts", "prevalence", "split_fingerprint",
              "preprocessing", "checksum", "spec"):
        assert k in m
    s = pd.read_parquet(root / "ml/samples.parquet")
    assert s.groupby("patient_id")["split"].nunique().max() == 1
    assert m["counts"]["patients"] == s["patient_id"].nunique() and m["counts"]["images"] == len(s)
    # no feature may be computed from the future, and no post-recognition samples remain
    assert (s["hours_to_recognition"].dropna() >= 0).all()
    assert s["thermal_npy_key"].notna().all()
    flow = pd.read_csv(root / "ml/cohort_flow.csv")
    assert flow["stage"].str.contains("screened").any() and flow["stage"].str.contains("split=test").any()


def test_builder_is_reproducible_and_lockable(built):
    from irtcri.pipeline.builder import LockedDatasetError, build_dataset, load_spec, lock_dataset, verify_locked
    s = built["session"]
    spec = load_spec("configs/datasets/primary_early_warning.yaml")
    spec.update(version="test-1")
    spec["images"]["derive_thermal"]["size"] = 64
    spec["images"]["derive_rgb"]["size"] = 64
    a = build_dataset(s, spec, now=pd.Timestamp("2026-09-01T00:00Z"))
    b = build_dataset(s, spec, now=pd.Timestamp("2026-09-01T00:00Z"))
    assert a["split_fingerprint"] == b["split_fingerprint"] and a["counts"] == b["counts"]
    sa = pd.read_parquet(a["root"] + "/ml/samples.parquet")
    lock_dataset(s, spec["name"], "test-1")
    assert verify_locked(a["root"])
    with pytest.raises(LockedDatasetError):
        build_dataset(s, spec)
    assert len(sa)


def _cfg(built, **kw) -> ExperimentCfg:
    base = {"name": "t", "family": "F0", "dataset_root": built["manifest"]["root"],
            "output_root": str(built["tmp"] / "runs"), "model": {"kind": "sklearn"}}
    base.update(kw)
    return ExperimentCfg.model_validate(base)


def test_clinical_baseline_positive_control(built):
    from irtcri.ml.train import train_experiment
    run = train_experiment(_cfg(built, name="f0b", model={"kind": "sklearn",
                                                          "clinical_feature_groups": ["clin", "base", "ep", "img"]}))
    card = json.loads((run / "model_card.json").read_text())
    assert card["test_set_status"].startswith("LOCKED")
    assert card["threshold_selection"]["selected_on"] == "validation"
    assert card["validation_metrics"]["auroc"]["estimate"] > 0.7, card["validation_metrics"]["auroc"]
    vp = pd.read_parquet(run / "val_predictions.parquet")
    samples = pd.read_parquet(built["manifest"]["root"] + "/ml/samples.parquet")
    test_ids = set(samples.loc[samples["split"] == "test", "thermal_image_id"])
    assert not (set(vp["thermal_image_id"]) & test_ids)
    built["f0_run"] = run


def test_locked_test_evaluation_requires_confirmation(built):
    from irtcri.ml.testlock import TestSetLockError, evaluate_locked_test
    run = built["f0_run"]
    with pytest.raises(TestSetLockError):
        evaluate_locked_test(run)
    res = evaluate_locked_test(run, confirm_final=True, n_boot=50)
    assert res["test_access"]["this_access"]["access_number"] == 1
    assert "event_sensitivity" in res["early_warning"]["summary"]
    res2 = evaluate_locked_test(run, confirm_final=True, n_boot=20)
    assert res2["test_access"]["previous_accesses"] == 1      # repeated access is visible


@pytest.mark.ml
def test_torch_multimodal_smoke_and_gradcam_replay(built):
    torch = pytest.importorskip("torch")
    from irtcri.core.security import SYSTEM_PRINCIPAL
    from irtcri.ml.infer import replay
    from irtcri.ml.train import LoadedModel, train_experiment
    cfg = _cfg(built, name="smoke", family="F5", model={"kind": "torch", "backbone": "simple_cnn",
                                                        "pretrained": False, "input_size": 64},
               training={"epochs": 2, "batch_size": 32, "lr": 0.001, "seed": 0})
    run = train_experiment(cfg)
    m = LoadedModel(run)
    samples = pd.read_parquet(built["manifest"]["root"] + "/ml/samples.parquet")
    p1 = m.predict(samples.head(8))
    p2 = LoadedModel(run).predict(samples.head(8))
    assert ((p1 >= 0) & (p1 <= 1)).all() and abs(p1 - p2).max() < 1e-6     # deterministic reload
    n = replay(built["session"], SYSTEM_PRINCIPAL, str(run), ("val",), limit=3)
    assert n == 3
    from sqlalchemy import select

    from irtcri.db.models import ModelPrediction
    preds = built["session"].scalars(select(ModelPrediction)).all()
    assert all(p.explanation_object_key for p in preds)
    assert torch.__version__


@pytest.mark.ml
def test_backbones_build_offline():
    pytest.importorskip("torch")
    import torch

    from irtcri.ml.models.fusion import MultimodalNet
    from irtcri.ml.models.unet import UNet, dice_loss
    for bb in ("resnet18", "efficientnet_b0"):
        net = MultimodalNet(["clinical", "thermal", "rgb"], bb, pretrained=False, n_clinical=5)
        out = net({"thermal": torch.rand(2, 1, 64, 64), "rgb": torch.rand(2, 3, 64, 64),
                   "rgb_present": torch.tensor([1.0, 0.0]), "clinical": torch.rand(2, 5)})
        assert out.shape == (2,)
    u = UNet(1, 1, base=8, depth=3)
    y = u(torch.rand(2, 1, 64, 64))
    assert y.shape == (2, 1, 64, 64) and float(dice_loss(y, torch.ones_like(y))) <= 1


def test_analysis_registry_requires_locked_dataset(built):
    from pathlib import Path

    from irtcri.core.security import Principal, Role
    from irtcri.evaluation.registry import register_analysis
    from irtcri.hub.services import Conflict
    s = built["session"]
    who = Principal(None, "stat01", Role.STATISTICIAN)
    root = built["manifest"]["root"]          # version test-0 is NOT locked
    out = Path(built["tmp"]) / "paper_out"
    out.mkdir()
    (out / "table2.csv").write_text("metric,value\nauroc,0.8\n")
    with pytest.raises(Conflict):
        register_analysis(s, who, "IRTCRI-P1", "1.0", root, "configs/analysis/primary_sap_v0.yaml", out)
    rec = register_analysis(s, who, "IRTCRI-P1", "0.1-draft", root, "configs/analysis/primary_sap_v0.yaml", out,
                            model_run_dir=built["f0_run"], allow_unlocked=True)
    s.commit()
    assert rec.dataset_version == "test-0" and rec.statistical_config["sap_version"] == "0.1-draft"
    assert rec.model_version.startswith("f0b@") and rec.outputs["table2.csv"] and rec.date_created
    assert rec.environment["packages"]["numpy"] and (out / "analysis_record.json").exists()
    with pytest.raises(Conflict):     # immutable: same paper/version cannot be re-registered
        register_analysis(s, who, "IRTCRI-P1", "0.1-draft", root, "configs/analysis/primary_sap_v0.yaml",
                          allow_unlocked=True)
