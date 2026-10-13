"""Experiment tracking: MLflow when available/configured, otherwise a local JSONL tracker.

MLflow is optional - a run directory with config, metrics and artefacts is
always written so experiments are reproducible without extra infrastructure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class LocalTracker:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._f = open(run_dir / "metrics.jsonl", "a")

    def log_params(self, params: dict[str, Any]) -> None:
        (self.run_dir / "params.json").write_text(json.dumps(params, indent=2, default=str))

    def log_metrics(self, metrics: dict[str, float], step: int | None = None) -> None:
        self._f.write(json.dumps({"step": step, **metrics}, default=str) + "\n")
        self._f.flush()

    def log_artifact(self, path: str | Path) -> None:
        pass  # already inside run_dir

    def end(self) -> None:
        self._f.close()


class MlflowTracker(LocalTracker):
    def __init__(self, run_dir: Path, experiment: str):
        super().__init__(run_dir)
        import mlflow
        self.mlflow = mlflow
        mlflow.set_experiment(experiment)
        self.run = mlflow.start_run(run_name=run_dir.name)

    def log_params(self, params):
        super().log_params(params)
        flat = {k: str(v)[:250] for k, v in _flatten(params).items()}
        self.mlflow.log_params(flat)

    def log_metrics(self, metrics, step=None):
        super().log_metrics(metrics, step)
        self.mlflow.log_metrics({k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))}, step=step)

    def log_artifact(self, path):
        self.mlflow.log_artifact(str(path))

    def end(self):
        super().end()
        self.mlflow.end_run()


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def make_tracker(run_dir: Path, experiment: str) -> LocalTracker:
    if os.environ.get("MLFLOW_TRACKING_URI"):
        try:
            return MlflowTracker(run_dir, experiment)
        except ImportError:
            pass
    return LocalTracker(run_dir)
