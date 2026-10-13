"""Automated, configurable image quality control.

Nothing is deleted. Every check emits a finding with a severity; the overall
status is the worst severity (PASS < WARNING < FAIL). Findings, metrics and the
config version are stored with the image.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import yaml
from PIL import Image

from irtcri.core.config import get_settings
from irtcri.imaging.readers import DecodedImage
from irtcri.schemas.dictionary import load_dictionary

SEVERITY_ORDER = {"PASS": 0, "WARNING": 1, "FAIL": 2}


@lru_cache
def load_qc_config(path: str | None = None) -> dict:
    p = Path(path) if path else get_settings().config_path(get_settings().qc_config_file)
    with open(p) as f:
        return yaml.safe_load(f)


@dataclass
class Finding:
    check: str
    severity: str
    message: str

    def as_dict(self) -> dict:
        return {"check": self.check, "severity": self.severity, "message": self.message}


@dataclass
class QCResult:
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    config_version: str = ""

    def add(self, check: str, severity: str, message: str) -> None:
        self.findings.append(Finding(check, severity, message))

    @property
    def status(self) -> str:
        worst = "PASS"
        for f in self.findings:
            if SEVERITY_ORDER[f.severity] > SEVERITY_ORDER[worst]:
                worst = f.severity
        return worst

    @property
    def reasons(self) -> list[dict]:
        return [f.as_dict() for f in self.findings if f.severity != "PASS"]


@dataclass
class QCContext:
    """Information from the database needed for relational checks."""
    now: datetime
    site_timezone: str = "UTC"
    insertion_time: datetime | None = None
    removal_time: datetime | None = None
    exact_duplicate_ids: list[str] = field(default_factory=list)
    # (image_id, acquisition_time, view, dhash_hex) for other images in the same episode
    episode_images: list[tuple[str, datetime, str, str | None]] = field(default_factory=list)


# ------------------------------------------------------------------ helpers
def dhash(gray: np.ndarray, size: int = 8) -> str:
    img = Image.fromarray(gray.astype(np.uint8)).resize((size + 1, size), Image.Resampling.LANCZOS)
    a = np.asarray(img, dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming_hex(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def to_gray_uint8(dec: DecodedImage) -> np.ndarray:
    if dec.temperature_c is not None:
        t = np.nan_to_num(dec.temperature_c, nan=float(np.nanmin(dec.temperature_c)) if np.isfinite(
            dec.temperature_c).any() else 0.0)
        lo, hi = np.percentile(t, [1, 99])
        if hi - lo < 1e-6:
            return np.zeros(t.shape, dtype=np.uint8)
        return (np.clip((t - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
    return np.asarray(Image.fromarray(dec.pixels).convert("L"))


def laplacian_variance(gray: np.ndarray, long_side: int = 512) -> float:
    img = Image.fromarray(gray)
    scale = long_side / max(img.size)
    if scale < 1:
        img = img.resize((int(img.size[0] * scale), int(img.size[1] * scale)), Image.Resampling.BILINEAR)
    g = np.asarray(img, dtype=np.float64)
    lap = -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(lap.var())


# --------------------------------------------------------------------- main
def run_qc(modality: str, dec: DecodedImage | None, read_error: str | None, meta: dict[str, Any],
           ctx: QCContext, file_format: str, size_bytes: int, config: dict | None = None) -> QCResult:
    cfg = config or load_qc_config()
    res = QCResult(config_version=cfg["version"])

    allowed = cfg["file"]["allowed_thermal_formats" if modality == "thermal" else "allowed_rgb_formats"]
    if file_format not in allowed:
        res.add("file_format", "FAIL", f"unexpected file format '{file_format}' (allowed {allowed})")
    if size_bytes < cfg["file"]["min_size_bytes"]:
        res.add("file_size", "FAIL", f"file too small ({size_bytes} bytes) - possibly truncated")
    if read_error or dec is None:
        res.add("decode", "FAIL", f"corrupted or undecodable file: {read_error}")
        _metadata_checks(res, cfg, modality, meta, ctx)
        _duplicate_checks(res, cfg, ctx, meta, None)
        return res

    res.metrics.update({"width": dec.width, "height": dec.height})
    key = "thermal" if modality == "thermal" else "rgb"
    mw, mh = cfg["dimensions"][f"{key}_min"]
    if dec.width < mw or dec.height < mh:
        res.add("dimensions", "FAIL", f"{dec.width}x{dec.height} below minimum {mw}x{mh}")
    expected = cfg["dimensions"].get(f"{key}_expected") or []
    if expected and [dec.width, dec.height] not in [list(e) for e in expected]:
        res.add("dimensions", "WARNING", f"{dec.width}x{dec.height} not an expected sensor resolution {expected}")

    if modality == "thermal":
        _thermal_checks(res, cfg, dec)
    else:
        _rgb_checks(res, cfg, dec)

    gray = to_gray_uint8(dec)
    res.metrics["dhash"] = dhash(gray)
    _metadata_checks(res, cfg, modality, meta, ctx)
    _timestamp_checks(res, cfg, meta, ctx, dec)
    _duplicate_checks(res, cfg, ctx, meta, res.metrics["dhash"])
    return res


def _thermal_checks(res: QCResult, cfg: dict, dec: DecodedImage) -> None:
    tc = cfg["thermal"]
    if not dec.is_radiometric or dec.temperature_c is None:
        res.add("radiometric", tc["non_radiometric_status"],
                "non-radiometric (palette) thermal image: absolute temperatures unavailable")
        return
    t = dec.temperature_c
    nan_frac = float(np.mean(~np.isfinite(t)))
    res.metrics["nan_fraction"] = nan_frac
    if nan_frac > tc["max_nan_fraction"]:
        res.add("invalid_pixels", "FAIL", f"{nan_frac:.1%} non-finite pixels")
    finite = t[np.isfinite(t)]
    if finite.size == 0:
        res.add("thermal_range", "FAIL", "no finite temperature values")
        return
    tmin, tmax, tmean, tstd = (float(finite.min()), float(finite.max()), float(finite.mean()), float(finite.std()))
    res.metrics.update({"t_min": tmin, "t_max": tmax, "t_mean": tmean, "t_std": tstd,
                        "t_p99": float(np.percentile(finite, 99))})
    lo, hi = tc["physical_range_c"]
    if tmin < lo or tmax > hi:
        res.add("thermal_range", "FAIL",
                f"temperatures [{tmin:.1f}, {tmax:.1f}] degC outside physical range {lo}..{hi} "
                "(wrong unit, e.g. Kelvin, or corrupted export?)")
        return
    smin, smax = tc["skin_max_plausible_c"]
    if not (smin <= tmax <= smax):
        res.add("extreme_temperature", "WARNING", f"max temperature {tmax:.1f} degC outside skin band {smin}..{smax}")
    mlo, mhi = tc["mean_plausible_c"]
    if not (mlo <= tmean <= mhi):
        res.add("abnormal_statistics", "WARNING", f"mean temperature {tmean:.1f} degC outside {mlo}..{mhi}")
    if tstd < tc["min_std_c"]:
        res.add("abnormal_statistics", "WARNING", f"near-uniform image (std {tstd:.2f} degC)")
    sat = float(np.mean(finite == tmin) + np.mean(finite == tmax))
    res.metrics["saturation_fraction"] = sat
    if sat > tc["max_saturation_fraction"] and tstd > 0:
        res.add("saturation", "WARNING", f"{sat:.1%} of pixels at range limits (clipping / span setting?)")


def _rgb_checks(res: QCResult, cfg: dict, dec: DecodedImage) -> None:
    rc = cfg["rgb"]
    gray = np.asarray(Image.fromarray(dec.pixels).convert("L"))
    mean, std = float(gray.mean()), float(gray.std())
    res.metrics.update({"brightness": mean, "gray_std": std})
    if std < rc["min_std"]:
        res.add("abnormal_statistics", "FAIL", f"blank/uniform RGB frame (std {std:.1f})")
        return
    lo, hi = rc["brightness_range"]
    if not (lo <= mean <= hi):
        res.add("exposure", "WARNING", f"mean brightness {mean:.0f} outside {lo}..{hi}")
    blur = laplacian_variance(gray)
    res.metrics["blur_score"] = blur
    if blur < rc["blur_laplacian_var_fail"]:
        res.add("blur", "FAIL", f"severely out of focus (Laplacian variance {blur:.1f})")
    elif blur < rc["blur_laplacian_var_warning"]:
        res.add("blur", "WARNING", f"possibly out of focus (Laplacian variance {blur:.1f})")


def _metadata_checks(res: QCResult, cfg: dict, modality: str, meta: dict, ctx: QCContext) -> None:
    mc = cfg["metadata"]
    for f in mc["fail_if_missing"]:
        if meta.get(f) in (None, ""):
            res.add("missing_metadata", "FAIL", f"required metadata '{f}' missing")
    warn = list(mc["warn_if_missing"]) + (list(mc["thermal_warn_if_missing"]) if modality == "thermal" else [])
    for f in warn:
        if meta.get(f) in (None, ""):
            res.add("missing_metadata", "WARNING", f"metadata '{f}' missing")
    d = load_dictionary()
    for f in ("ambient_temperature_c", "distance_cm", "emissivity", "relative_humidity_pct"):
        v = meta.get(f)
        if v is None or f not in d:
            continue
        var = d.get(f)
        if var.hard_range and not (var.hard_range[0] <= v <= var.hard_range[1]):
            res.add("metadata_range", "FAIL", f"{f}={v} impossible (outside {list(var.hard_range)})")
        elif var.plausible_range and not (var.plausible_range[0] <= v <= var.plausible_range[1]):
            res.add("metadata_range", "WARNING", f"{f}={v} outside protocol range {list(var.plausible_range)}")


def _timestamp_checks(res: QCResult, cfg: dict, meta: dict, ctx: QCContext, dec: DecodedImage) -> None:
    tc = cfg["timestamps"]
    t: datetime | None = meta.get("acquisition_time")
    if t is None:
        return
    if t > ctx.now + timedelta(minutes=tc["future_tolerance_minutes"]):
        res.add("timestamp", "FAIL", f"acquisition_time {t.isoformat()} is in the future")
    if ctx.insertion_time and t < ctx.insertion_time:
        res.add("timestamp", "FAIL", "acquisition_time precedes catheter insertion")
    if ctx.removal_time and t > ctx.removal_time + timedelta(hours=tc["removal_grace_hours"]):
        res.add("timestamp", "FAIL", "acquisition_time after catheter removal")
    if dec.exif_time is not None:
        exif_utc = dec.exif_time.replace(tzinfo=ZoneInfo(ctx.site_timezone))
        diff = abs((exif_utc - t).total_seconds()) / 60
        res.metrics["exif_time_diff_min"] = round(diff, 1)
        if diff > tc["exif_mismatch_warning_minutes"]:
            res.add("timestamp", "WARNING",
                    f"camera EXIF time differs from declared acquisition time by {diff:.0f} min "
                    "(camera clock drift or wrong timezone?)")
    view = meta.get("view")
    for _id, other_t, other_view, _ in ctx.episode_images:
        if other_view == view and abs((other_t - t).total_seconds()) < tc["min_interval_same_view_minutes"] * 60:
            res.add("timestamp", "WARNING", f"another '{view}' image of this episode within "
                    f"{tc['min_interval_same_view_minutes']} min (image {_id})")
            break


def _duplicate_checks(res: QCResult, cfg: dict, ctx: QCContext, meta: dict, h: str | None) -> None:
    dc = cfg["duplicates"]
    if ctx.exact_duplicate_ids:
        res.add("duplicate", dc["exact"], f"byte-identical to already stored image(s) {ctx.exact_duplicate_ids}")
    if h is None:
        return
    t = meta.get("acquisition_time")
    for _id, other_t, _view, other_h in ctx.episode_images:
        if other_h is None or _id in ctx.exact_duplicate_ids:
            continue
        if t is not None and abs((other_t - t).total_seconds()) > dc["near_window_minutes"] * 60:
            continue
        d = hamming_hex(h, other_h)
        if d <= dc["near_hamming_max"]:
            res.add("near_duplicate", "WARNING", f"visually near-identical to image {_id} (dHash distance {d})")
            break
