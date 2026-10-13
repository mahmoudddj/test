"""Locked test-set evaluation.

The test (and external_test) split is never read during development. Final
evaluation requires an explicit confirmation, verifies the model was trained on
exactly this dataset version and split, and appends an entry to an append-only
ledger next to the dataset - every access to the test set is visible in the
evaluation report.
"""

from __future__ import annotations

import getpass
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from irtcri.evaluation.report import evaluate_predictions
from irtcri.ml.train import LoadedModel
from irtcri.pipeline.builder import load_ml_dataset


class TestSetLockError(PermissionError):
    pass


def episodes_for_evaluation(dataset_root: str | Path) -> pd.DataFrame:
    root = Path(dataset_root)
    tte = pd.read_parquet(root / "longitudinal" / "time_to_event.parquet")
    ep = tte[["episode_id", "patient_id", "site_id", "catheter_type", "start_time", "stop_time",
                                 "event"]].copy()
    ep["recognition_time"] = tte["stop_time"].where(tte["event"] == 1)
    return ep


def evaluate_locked_test(run_dir: str | Path, confirm_final: bool = False, split: str = "test",
                         n_boot: int = 2000, out_dir: str | Path | None = None) -> dict:
    if not confirm_final:
        raise TestSetLockError("Final test evaluation requires confirm_final=True (CLI: --confirm-final). "
                               "Use the validation split for model development.")
    model = LoadedModel(run_dir)
    ds = model.card["dataset"]
    samples, _, manifest = load_ml_dataset(ds["root"])
    if manifest["checksum"] != ds["checksum"] or manifest["split_fingerprint"] != ds["split_fingerprint"]:
        raise TestSetLockError("dataset or split changed since training - test evaluation refused")
    ledger = Path(ds["root"]) / "test_access_ledger.jsonl"
    previous = [json.loads(x) for x in ledger.read_text().splitlines()] if ledger.exists() else []
    entry = {"time": datetime.now(timezone.utc).isoformat(), "user": getpass.getuser(), "run_dir": str(run_dir),
             "model": model.card["model_name"], "split": split, "access_number": len(previous) + 1}
    with open(ledger, "a") as f:
        f.write(json.dumps(entry) + "\n")
    test = samples[samples["split"] == split].reset_index(drop=True)
    if test.empty:
        raise ValueError(f"split '{split}' is empty")
    ep = episodes_for_evaluation(ds["root"])
    cols = ["thermal_image_id", "patient_id", "episode_id", "site_id", "site_code", "prediction_time", "label"]
    pred = test[cols].assign(prob=model.predict(test))
    pred = pred.merge(ep[["episode_id", "catheter_type"]], on="episode_id", how="left")
    out = Path(out_dir or Path(run_dir) / f"final_{split}_evaluation")
    out.mkdir(parents=True, exist_ok=True)
    pred.to_parquet(out / "predictions.parquet", index=False)
    res = evaluate_predictions(pred, ep[ep["episode_id"].isin(pred["episode_id"])], model.threshold, out,
                               f"{model.card['model_name']} ({model.card['family']})", split, n_boot=n_boot)
    res["test_access"] = {"this_access": entry, "previous_accesses": len(previous)}
    (out / "test_access.json").write_text(json.dumps(res["test_access"], indent=2))
    return res
