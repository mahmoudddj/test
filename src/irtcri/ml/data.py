"""Dataset access for ML: clinical preprocessing (fit on train only) and torch datasets."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from irtcri.imaging.derive import load_derived

DEV_SPLITS = ("train", "val")
LOCKED_SPLITS = ("test", "external_test")


def development_only(samples: pd.DataFrame) -> pd.DataFrame:
    """Drop locked evaluation splits before any modelling step touches the data."""
    return samples[samples["split"].isin(DEV_SPLITS)].reset_index(drop=True)


def clinical_columns(spec: dict, groups: list[str]) -> list[str]:
    cols: list[str] = []
    for g in groups:
        cols += spec["groups"].get(g, [])
    return cols


class ClinicalPreprocessor:
    """Median imputation + standardisation; fitted on the TRAINING split only."""

    def __init__(self, columns: list[str]):
        self.columns = columns
        self.pipe = Pipeline([("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                              ("scale", StandardScaler())])

    def fit(self, df: pd.DataFrame) -> "ClinicalPreprocessor":
        self.pipe.fit(df[self.columns].astype(float))
        return self

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        return self.pipe.transform(df[self.columns].astype(float)).astype(np.float32)


@lru_cache(maxsize=4096)
def _cached(key: str, sha: str | None) -> np.ndarray:
    return load_derived(key, sha)


def load_image(key: str | None, sha: str | None, modality: str) -> np.ndarray | None:
    if not isinstance(key, str):
        return None
    arr = _cached(key, sha)
    if modality == "thermal":
        return arr.astype(np.float32)[None]                     # 1xHxW in [0,1]
    return (arr.astype(np.float32) / 255.0).transpose(2, 0, 1)  # 3xHxW


try:
    import torch
    from torch.utils.data import Dataset

    class MultimodalDataset(Dataset):
        def __init__(self, samples: pd.DataFrame, modalities: list[str], clinical: np.ndarray | None,
                     input_size: int, augment=None, window_c: tuple[float, float] = (20.0, 40.0)):
            self.s = samples.reset_index(drop=True)
            self.modalities = modalities
            self.clinical = clinical
            self.input_size = input_size
            self.augment = augment
            self.window_w = window_c[1] - window_c[0]

        def __len__(self) -> int:
            return len(self.s)

        def _img(self, r, modality: str) -> tuple[torch.Tensor, float]:
            key, sha = (r["thermal_npy_key"], r["thermal_npy_sha256"]) if modality == "thermal" else (
                r["rgb_npy_key"], r["rgb_npy_sha256"])
            arr = load_image(key, sha, modality)
            ch = 1 if modality == "thermal" else 3
            if arr is None:
                return torch.zeros(ch, self.input_size, self.input_size), 0.0
            x = torch.from_numpy(arr)
            if x.shape[-1] != self.input_size:
                x = torch.nn.functional.interpolate(x[None], size=(self.input_size, self.input_size),
                                                    mode="bilinear", align_corners=False)[0]
            return x, 1.0

        def __getitem__(self, i: int) -> dict:
            r = self.s.iloc[i]
            item = {"label": torch.tensor(float(r["label"])), "idx": torch.tensor(i)}
            if "thermal" in self.modalities:
                item["thermal"], _ = self._img(r, "thermal")
            if "rgb" in self.modalities:
                item["rgb"], item["rgb_present"] = self._img(r, "rgb")
                item["rgb_present"] = torch.tensor(item["rgb_present"])
            if self.augment is not None:
                item = self.augment(item, self.window_w)
            if "clinical" in self.modalities:
                item["clinical"] = torch.from_numpy(self.clinical[i])
            return item

    class Augmenter:
        """Geometric augmentation applied identically to registered thermal and RGB inputs.

        No photometric jitter on thermal values by default (absolute temperature is the signal);
        an optional global offset simulates camera calibration differences.
        """

        def __init__(self, cfg, seed: int):
            self.cfg = cfg
            self.g = torch.Generator().manual_seed(seed)

        def _rand(self, lo: float, hi: float) -> float:
            return float(lo + (hi - lo) * torch.rand(1, generator=self.g))

        def __call__(self, item: dict, window_w: float) -> dict:
            import torchvision.transforms.functional as TF
            c = self.cfg
            angle = self._rand(-c.rotate_deg, c.rotate_deg)
            scale = self._rand(*c.scale)
            keys = [k for k in ("thermal", "rgb") if k in item]
            size = item[keys[0]].shape[-1] if keys else 1
            tx, ty = (int(self._rand(-c.translate, c.translate) * size) for _ in range(2))
            flip = c.hflip and torch.rand(1, generator=self.g) < 0.5
            for k in keys:
                x = item[k]
                if flip:
                    x = torch.flip(x, dims=[-1])
                x = TF.affine(x, angle=angle, translate=[tx, ty], scale=scale, shear=[0.0],
                              interpolation=TF.InterpolationMode.BILINEAR, fill=float(x.min()))
                if c.noise_std > 0:
                    x = x + c.noise_std * torch.randn(x.shape, generator=self.g)
                if k == "thermal" and c.temp_offset_c > 0:
                    x = x + self._rand(-c.temp_offset_c, c.temp_offset_c) / window_w
                item[k] = x
            return item
except ImportError:  # torch is optional for clinical-only baselines
    torch = None
