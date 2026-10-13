"""Training configuration schema (YAML -> validated pydantic model)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Modality = Literal["clinical", "thermal", "rgb"]
FAMILIES = {
    "F0": ["clinical"], "F1": ["thermal"], "F2": ["rgb"], "F3": ["thermal", "rgb"],
    "F4": ["clinical", "thermal"], "F5": ["clinical", "thermal", "rgb"],
}


class Augmentation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hflip: bool = False            # off by default: laterality is clinically meaningful
    rotate_deg: float = 10.0
    translate: float = 0.05
    scale: tuple[float, float] = (0.9, 1.1)
    noise_std: float = 0.0         # in normalised units
    temp_offset_c: float = 0.0     # simulated calibration offset (thermal only); window width converts to units


class ModelCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["sklearn", "torch"] = "torch"
    sklearn_estimator: Literal["logistic", "hgb"] = "logistic"
    backbone: Literal["simple_cnn", "resnet18", "resnet34", "resnet50", "efficientnet_b0", "vit_b_16"] = "resnet18"
    pretrained: bool = True
    input_size: int = 224
    dropout: float = 0.2
    clinical_hidden: int = 64
    fusion_hidden: int = 128
    clinical_feature_groups: list[str] = Field(default_factory=lambda: ["clin", "base", "ep"])
    freeze_backbone_epochs: int = 0


class TrainCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = 42
    epochs: int = 30
    batch_size: int = 16
    lr: float = 1e-4
    weight_decay: float = 1e-4
    early_stopping_patience: int = 6
    monitor: Literal["val_auroc", "val_loss", "val_auprc"] = "val_auroc"
    imbalance: Literal["pos_weight", "weighted_sampler", "none"] = "pos_weight"
    augmentation: Augmentation = Field(default_factory=Augmentation)
    num_workers: int = 0
    deterministic: bool = True
    device: str = "auto"


class ThresholdCfg(BaseModel):
    rule: Literal["target_sensitivity", "target_precision", "youden", "max_f1", "fixed"] = "target_sensitivity"
    target: float = 0.85


class ExperimentCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    family: Literal["F0", "F1", "F2", "F3", "F4", "F5"]
    modalities: list[Modality] | None = None
    dataset_root: str
    output_root: str = "var/runs"
    model: ModelCfg = Field(default_factory=ModelCfg)
    training: TrainCfg = Field(default_factory=TrainCfg)
    threshold: ThresholdCfg = Field(default_factory=ThresholdCfg)
    calibration: Literal["none", "platt"] = "none"
    intended_use: str = ("Research use only: retrospective evaluation of thermal-imaging-based early warning "
                         "of catheter exit-site / catheter-related infection in the IRT-in-CRI Study.")
    limitations: str = ("Not validated for clinical decision making. Trained on a small multicentre pilot cohort; "
                        "performance may not transfer to other cameras, sites, skin types or catheter types.")

    def resolved_modalities(self) -> list[str]:
        return list(self.modalities or FAMILIES[self.family])


def load_experiment(path: str | Path, overrides: dict | None = None) -> ExperimentCfg:
    raw = yaml.safe_load(open(path))
    for k, v in (overrides or {}).items():
        cur = raw
        parts = k.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = v
    return ExperimentCfg.model_validate(raw)
