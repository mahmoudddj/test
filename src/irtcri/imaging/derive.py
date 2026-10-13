"""Derived (preprocessed) images with complete provenance.

The raw object is never modified. Each derivative records parent checksum,
parameters, preprocessing version and code version; derivatives are cached by
(parent, derivation, preprocessing_version).
"""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.provenance import git_commit, sha256_bytes, sha256_json
from irtcri.db.models import DerivedImage, ThermalImage
from irtcri.imaging.ingest import load_temperature
from irtcri.imaging.storage import derived_bucket, get_object_store


@dataclass(frozen=True)
class ThermalPreprocessConfig:
    size: int = 256
    # fixed_window: (T - lo)/(hi - lo), clipped -> comparable across images/sites (recommended);
    # ambient_delta: T - ambient, then fixed window on the delta;
    # per_image_zscore: removes absolute temperature information (use only as ablation).
    normalization: str = "fixed_window"
    window_c: tuple[float, float] = (20.0, 40.0)
    delta_window_c: tuple[float, float] = (-5.0, 15.0)
    nan_fill: str = "image_min"
    keep_aspect: bool = True

    @property
    def version(self) -> str:
        return "thpp-" + sha256_json(asdict(self))[:12]


def resize_array(arr: np.ndarray, size: int, keep_aspect: bool = True) -> np.ndarray:
    """Resize a float array to size x size (letterboxed with edge padding when keep_aspect)."""
    h, w = arr.shape
    if keep_aspect:
        scale = size / max(h, w)
        nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
        img = Image.fromarray(arr.astype(np.float32), mode="F").resize((nw, nh), Image.Resampling.BILINEAR)
        small = np.asarray(img)
        top, left = (size - nh) // 2, (size - nw) // 2
        return np.pad(small, ((top, size - nh - top), (left, size - nw - left)), mode="edge")
    img = Image.fromarray(arr.astype(np.float32), mode="F").resize((size, size), Image.Resampling.BILINEAR)
    return np.asarray(img)


def preprocess_temperature(t: np.ndarray, cfg: ThermalPreprocessConfig,
                           ambient_c: float | None = None) -> np.ndarray:
    t = t.astype(np.float32).copy()
    if not np.isfinite(t).all():
        fill = float(np.nanmin(t)) if np.isfinite(t).any() else cfg.window_c[0]
        t[~np.isfinite(t)] = fill
    t = resize_array(t, cfg.size, cfg.keep_aspect)
    if cfg.normalization == "fixed_window":
        lo, hi = cfg.window_c
        out = (t - lo) / (hi - lo)
    elif cfg.normalization == "ambient_delta":
        if ambient_c is None:
            raise ValueError("ambient_delta normalisation requires ambient temperature")
        lo, hi = cfg.delta_window_c
        out = (t - ambient_c - lo) / (hi - lo)
    elif cfg.normalization == "per_image_zscore":
        out = (t - t.mean()) / (t.std() + 1e-6)
        return out.astype(np.float32)
    else:
        raise ValueError(f"unknown normalization {cfg.normalization}")
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def derive_thermal(session: Session, image: ThermalImage, cfg: ThermalPreprocessConfig) -> DerivedImage:
    existing = session.scalar(select(DerivedImage).where(
        DerivedImage.parent_image_id == image.id, DerivedImage.derivation == "thermal_preprocess",
        DerivedImage.preprocessing_version == cfg.version))
    if existing is not None:
        return existing
    dec = load_temperature(image)
    if dec.temperature_c is None:
        raise ValueError(f"image {image.id} is not radiometric; cannot derive temperature input")
    arr = preprocess_temperature(dec.temperature_c, cfg, image.ambient_temperature_c)
    buf = io.BytesIO()
    np.save(buf, arr.astype(np.float16), allow_pickle=False)
    data = buf.getvalue()
    key = f"thermal_preprocess/{cfg.version}/{image.raw_sha256}.npy"
    get_object_store().put(derived_bucket(), key, data)
    d = DerivedImage(parent_modality="thermal", parent_image_id=image.id, parent_sha256=image.raw_sha256,
                     derivation="thermal_preprocess", params=asdict(cfg), preprocessing_version=cfg.version,
                     code_version=git_commit(short=True), object_key=key, sha256=sha256_bytes(data))
    session.add(d)
    session.flush()
    return d


def load_derived(object_key: str, sha256: str | None = None) -> np.ndarray:
    data = get_object_store().get(derived_bucket(), object_key)
    if sha256 is not None and sha256_bytes(data) != sha256:
        raise RuntimeError(f"checksum mismatch for derived object {object_key}")
    return np.load(io.BytesIO(data), allow_pickle=False)


@dataclass(frozen=True)
class RGBPreprocessConfig:
    size: int = 256
    keep_aspect: bool = True

    @property
    def version(self) -> str:
        return "rgbpp-" + sha256_json(asdict(self))[:12]


def preprocess_rgb(pixels: np.ndarray, cfg: RGBPreprocessConfig) -> np.ndarray:
    img = Image.fromarray(pixels)
    if cfg.keep_aspect:
        img.thumbnail((cfg.size, cfg.size), Image.Resampling.BILINEAR)
        canvas = Image.new("RGB", (cfg.size, cfg.size))
        canvas.paste(img, ((cfg.size - img.size[0]) // 2, (cfg.size - img.size[1]) // 2))
        img = canvas
    else:
        img = img.resize((cfg.size, cfg.size), Image.Resampling.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


def derive_rgb(session: Session, image, cfg: RGBPreprocessConfig) -> DerivedImage:
    from irtcri.imaging.ingest import load_rgb
    existing = session.scalar(select(DerivedImage).where(
        DerivedImage.parent_image_id == image.id, DerivedImage.derivation == "rgb_preprocess",
        DerivedImage.preprocessing_version == cfg.version))
    if existing is not None:
        return existing
    arr = preprocess_rgb(load_rgb(image).pixels, cfg)
    buf = io.BytesIO()
    np.save(buf, arr, allow_pickle=False)
    data = buf.getvalue()
    key = f"rgb_preprocess/{cfg.version}/{image.raw_sha256}.npy"
    get_object_store().put(derived_bucket(), key, data)
    d = DerivedImage(parent_modality="rgb", parent_image_id=image.id, parent_sha256=image.raw_sha256,
                     derivation="rgb_preprocess", params=asdict(cfg), preprocessing_version=cfg.version,
                     code_version=git_commit(short=True), object_key=key, sha256=sha256_bytes(data))
    session.add(d)
    session.flush()
    return d


def unletterbox(arr: np.ndarray, raw_shape: tuple[int, int]) -> np.ndarray:
    """Map a square, letterboxed model-space map back onto the raw image aspect ratio."""
    S = arr.shape[0]
    h, w = raw_shape
    scale = S / max(h, w)
    nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
    top, left = (S - nh) // 2, (S - nw) // 2
    return arr[top:top + nh, left:left + nw]
