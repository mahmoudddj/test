"""Reproducible training of model families F0-F5.

Guarantees:
* only the ``train`` and ``val`` splits are loaded (test/external_test are locked);
* patient-level split integrity is re-checked;
* seeds + deterministic algorithms; full config, dataset version/checksum, split
  fingerprint and code version are stored with every run;
* the operating threshold (and optional Platt calibration) is chosen on the
  validation split; the test split is evaluated only via ``ml.testlock``.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml

from irtcri.core.provenance import code_version
from irtcri.evaluation.calibration import PlattCalibrator
from irtcri.evaluation.metrics import binary_metrics, metrics_with_ci
from irtcri.ml.config import ExperimentCfg
from irtcri.ml.data import ClinicalPreprocessor, clinical_columns, development_only
from irtcri.ml.thresholds import select_threshold
from irtcri.ml.tracking import make_tracker
from irtcri.monitoring.dq import json_safe
from irtcri.pipeline.builder import load_ml_dataset
from irtcri.pipeline.splits import assert_no_patient_leakage


def set_seeds(seed: int, deterministic: bool = True) -> None:
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch
        torch.manual_seed(seed)
        if deterministic:
            torch.use_deterministic_algorithms(True, warn_only=True)
            torch.backends.cudnn.benchmark = False
    except ImportError:
        pass


def _device(pref: str):
    import torch
    if pref != "auto":
        return torch.device(pref)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def train_experiment(cfg: ExperimentCfg, run_name: str | None = None) -> Path:
    samples, spec, manifest = load_ml_dataset(cfg.dataset_root)
    dev = development_only(samples)
    assert_no_patient_leakage(dev)
    train = dev[dev["split"] == "train"].reset_index(drop=True)
    val = dev[dev["split"] == "val"].reset_index(drop=True)
    if train.empty or val.empty:
        raise ValueError("train and val splits must both be non-empty")
    set_seeds(cfg.training.seed, cfg.training.deterministic)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(cfg.output_root) / cfg.name / (run_name or stamp)
    tracker = make_tracker(run_dir, cfg.name)
    modalities = cfg.resolved_modalities()
    (run_dir / "config.yaml").write_text(yaml.safe_dump(json.loads(cfg.model_dump_json()), sort_keys=False))
    tracker.log_params(json.loads(cfg.model_dump_json()))

    clin_cols = clinical_columns(spec, cfg.model.clinical_feature_groups) if "clinical" in modalities else []
    prep = ClinicalPreprocessor(clin_cols).fit(train) if clin_cols else None
    if prep is not None:
        joblib.dump(prep, run_dir / "clinical_preprocessor.joblib")

    extra: dict = {}
    if cfg.model.kind == "sklearn":
        if modalities != ["clinical"]:
            raise ValueError("sklearn models support the clinical modality only (family F0)")
        from irtcri.ml.models.clinical import make_clinical_estimator
        est = make_clinical_estimator(cfg.model.sklearn_estimator, cfg.training.seed,
                                      "balanced" if cfg.training.imbalance != "none" else None)
        est.fit(prep.transform(train), train["label"].astype(int))
        joblib.dump(est, run_dir / "model.joblib")
        p_val = est.predict_proba(prep.transform(val))[:, 1]
    else:
        p_val, extra = _train_torch(cfg, modalities, train, val, prep, run_dir, tracker)

    calib = None
    if cfg.calibration == "platt" and len(np.unique(val["label"])) == 2:
        calib = PlattCalibrator().fit(val["label"], p_val)
        p_val = calib.transform(p_val)
        joblib.dump(calib, run_dir / "calibrator.joblib")
    thr, thr_info = select_threshold(val["label"], p_val, cfg.threshold.rule, cfg.threshold.target)
    cols = ["thermal_image_id", "patient_id", "episode_id", "site_id", "site_code", "prediction_time", "label"]
    val_pred = val[cols].assign(prob=p_val)
    val_pred.to_parquet(run_dir / "val_predictions.parquet", index=False)
    val_metrics = metrics_with_ci(val_pred, thr, n_boot=500, seed=cfg.training.seed)
    tracker.log_metrics({f"val_{k}": v["estimate"] for k, v in val_metrics.items() if "estimate" in v})

    card = {
        "model_name": cfg.name, "model_version": run_dir.name, "family": cfg.family, "modalities": modalities,
        "created_time": datetime.now(timezone.utc).isoformat(),
        "dataset": {"name": manifest["name"], "version": manifest["version"], "checksum": manifest["checksum"],
                    "split_fingerprint": manifest["split_fingerprint"], "root": str(cfg.dataset_root),
                    "data_cutoff_time": manifest.get("data_cutoff_time")},
        "code_version": code_version(), "hyperparameters": json.loads(cfg.model_dump_json()),
        "clinical_features": clin_cols, "intended_use": cfg.intended_use, "limitations": cfg.limitations,
        "threshold": thr, "threshold_selection": thr_info, "calibration": calib.to_dict() if calib else None,
        "validation_population": {
            "split": "val", "patients": int(val["patient_id"].nunique()), "images": int(len(val)),
            "sites": sorted(val["site_id"].unique().tolist()), "positive_samples": int(val["label"].sum())},
        "validation_metrics": val_metrics,
        "test_set_status": "LOCKED - not evaluated (use `irtcri evaluate-test --confirm-final`)",
        **extra,
    }
    (run_dir / "model_card.json").write_text(json.dumps(json_safe(card), indent=2))
    tracker.end()
    return run_dir


def _make_model(cfg: ExperimentCfg, modalities: list[str], n_clin: int):
    from irtcri.ml.models.fusion import MultimodalNet
    return MultimodalNet(modalities, cfg.model.backbone, cfg.model.pretrained, n_clin, cfg.model.clinical_hidden,
                         cfg.model.fusion_hidden, cfg.model.dropout)


def _loader(df, cfg, modalities, prep, train: bool, window_c=(20.0, 40.0)):
    import torch
    from torch.utils.data import DataLoader, WeightedRandomSampler

    from irtcri.ml.data import Augmenter, MultimodalDataset
    clin = prep.transform(df) if prep is not None else None
    aug = Augmenter(cfg.training.augmentation, cfg.training.seed) if train else None
    ds = MultimodalDataset(df, modalities, clin, cfg.model.input_size, aug, window_c)
    g = torch.Generator().manual_seed(cfg.training.seed)
    sampler = None
    if train and cfg.training.imbalance == "weighted_sampler":
        y = df["label"].to_numpy()
        w = np.where(y == 1, 0.5 / max(y.sum(), 1), 0.5 / max((1 - y).sum(), 1))
        sampler = WeightedRandomSampler(torch.tensor(w, dtype=torch.double), len(df), replacement=True, generator=g)
    return DataLoader(ds, batch_size=cfg.training.batch_size, shuffle=train and sampler is None, sampler=sampler,
                      generator=g, num_workers=cfg.training.num_workers)


def _predict_loader(model, loader, device) -> np.ndarray:
    import torch
    model.eval()
    out = []
    with torch.no_grad():
        for b in loader:
            b = {k: v.to(device) for k, v in b.items()}
            out.append(torch.sigmoid(model(b)).cpu().numpy())
    return np.concatenate(out) if out else np.array([])


def _train_torch(cfg: ExperimentCfg, modalities, train, val, prep, run_dir: Path, tracker):
    import torch
    device = _device(cfg.training.device)
    model = _make_model(cfg, modalities, len(prep.columns) if prep else 0).to(device)
    pretrained = {k: getattr(getattr(model, k), "pretrained_loaded", None)
                  for k in ("thermal_enc", "rgb_enc") if hasattr(model, k)}
    tl = _loader(train, cfg, modalities, prep, True)
    vl = _loader(val, cfg, modalities, prep, False)
    pos = float(train["label"].sum())
    pw = torch.tensor((len(train) - pos) / max(pos, 1.0)) if cfg.training.imbalance == "pos_weight" else None
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pw.to(device) if pw is not None else None)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg.training.lr, weight_decay=cfg.training.weight_decay)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(cfg.training.epochs, 1))
    best, best_state, best_epoch, bad = -np.inf, None, -1, 0
    history = []
    for epoch in range(cfg.training.epochs):
        model.train()
        frozen = epoch < cfg.model.freeze_backbone_epochs
        for name in ("thermal_enc", "rgb_enc"):
            if hasattr(model, name):
                for p_ in getattr(model, name).parameters():
                    p_.requires_grad = not frozen
        tot, n = 0.0, 0
        for b in tl:
            b = {k: v.to(device) for k, v in b.items()}
            opt.zero_grad()
            loss = loss_fn(model(b), b["label"])
            loss.backward()
            opt.step()
            tot += float(loss.detach()) * len(b["label"])
            n += len(b["label"])
        sched.step()
        p_val = _predict_loader(model, vl, device)
        y_val = val["label"].to_numpy()
        m = binary_metrics(y_val, p_val, 0.5)
        vloss = float(torch.nn.functional.binary_cross_entropy(torch.tensor(np.clip(p_val, 1e-6, 1 - 1e-6)),
                                                                torch.tensor(y_val, dtype=torch.float32)))
        rec = {"epoch": epoch, "train_loss": tot / max(n, 1), "val_loss": vloss, "val_auroc": m["auroc"],
               "val_auprc": m["auprc"]}
        history.append(rec)
        tracker.log_metrics(rec, step=epoch)
        score = {"val_auroc": m["auroc"], "val_auprc": m["auprc"], "val_loss": -vloss}[cfg.training.monitor]
        score = -np.inf if not np.isfinite(score) else score
        if score > best or best_state is None:
            best, best_epoch, bad = score, epoch, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            torch.save(best_state, run_dir / "model.pt")
        else:
            bad += 1
            if bad >= cfg.training.early_stopping_patience:
                break
    model.load_state_dict(best_state)
    pd.DataFrame(history).to_csv(run_dir / "training_history.csv", index=False)
    return _predict_loader(model, vl, device), {"best_epoch": best_epoch, "epochs_run": len(history),
                                                "pretrained_weights_loaded": pretrained, "device": str(device)}


# ---------------------------------------------------------------------------- inference helpers
class LoadedModel:
    """A trained run loaded for prediction (validation, locked test, retrospective replay)."""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.card = json.loads((self.run_dir / "model_card.json").read_text())
        self.cfg = ExperimentCfg.model_validate(yaml.safe_load((self.run_dir / "config.yaml").read_text()))
        self.modalities = self.card["modalities"]
        p = self.run_dir / "clinical_preprocessor.joblib"
        self.prep = joblib.load(p) if p.exists() else None
        c = self.run_dir / "calibrator.joblib"
        self.calibrator = joblib.load(c) if c.exists() else None
        self.threshold = float(self.card["threshold"])
        if self.cfg.model.kind == "sklearn":
            self.est = joblib.load(self.run_dir / "model.joblib")
            self.net = None
        else:
            import torch
            self.net = _make_model(self.cfg.model_copy(update={"model": self.cfg.model.model_copy(
                update={"pretrained": False})}), self.modalities, len(self.prep.columns) if self.prep else 0)
            self.net.load_state_dict(torch.load(self.run_dir / "model.pt", map_location="cpu"))
            self.net.eval()

    def predict(self, samples: pd.DataFrame) -> np.ndarray:
        if self.net is None:
            p = self.est.predict_proba(self.prep.transform(samples))[:, 1]
        else:
            import torch
            loader = _loader(samples.reset_index(drop=True), self.cfg, self.modalities, self.prep, False)
            p = _predict_loader(self.net, loader, torch.device("cpu"))
        if self.calibrator is not None:
            p = self.calibrator.transform(p)
        return p
