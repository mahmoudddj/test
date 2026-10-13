"""Hand-crafted, interpretable thermal features from annotated ROIs.

These provide a strong, explainable baseline (and covariates for statistical
models) before deep learning: e.g. the exit-site vs reference-skin temperature
difference (delta-T) is the classic thermographic marker of local inflammation.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw


def roi_mask(shape: tuple[int, int], roi: dict | None) -> np.ndarray | None:
    if not roi:
        return None
    h, w = shape
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    if roi.get("type") == "polygon" and len(roi.get("points", [])) >= 3:
        d.polygon([tuple(p) for p in roi["points"]], fill=1)
    elif roi.get("type") == "circle":
        x, y, r = roi["x"], roi["y"], roi["r"]
        d.ellipse([x - r, y - r, x + r, y + r], fill=1)
    elif roi.get("type") == "bbox":
        d.rectangle([roi["x0"], roi["y0"], roi["x1"], roi["y1"]], fill=1)
    else:
        return None
    return np.asarray(m, dtype=bool)


def thermal_roi_features(t: np.ndarray, roi: dict | None, reference_roi: dict | None = None,
                         insertion_point: dict | None = None, ambient_c: float | None = None,
                         radius_px: int = 10) -> dict[str, float]:
    """Features in degC. Falls back to a disc around the insertion point when no ROI is drawn."""
    feats: dict[str, float] = {}
    if roi is None and insertion_point is not None:
        roi = {"type": "circle", "x": insertion_point["x"], "y": insertion_point["y"], "r": radius_px}
    m = roi_mask(t.shape, roi)
    finite = np.isfinite(t)
    if m is None or not (m & finite).any():
        return {"roi_available": 0.0}
    vals = t[m & finite]
    feats.update(roi_available=1.0, roi_t_max=float(vals.max()), roi_t_mean=float(vals.mean()),
                 roi_t_p90=float(np.percentile(vals, 90)), roi_t_std=float(vals.std()),
                 roi_area_px=float(m.sum()))
    ref_m = roi_mask(t.shape, reference_roi)
    if ref_m is None:
        # annulus around ROI as surrogate reference skin
        ys, xs = np.nonzero(m)
        cy, cx = ys.mean(), xs.mean()
        r = np.sqrt(m.sum() / np.pi)
        yy, xx = np.mgrid[: t.shape[0], : t.shape[1]]
        dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        ref_m = (dist > 2 * r) & (dist < 3.5 * r)
        feats["reference_is_annulus"] = 1.0
    else:
        feats["reference_is_annulus"] = 0.0
    ref = t[ref_m & finite]
    if ref.size:
        feats["ref_t_mean"] = float(ref.mean())
        feats["delta_t_max"] = feats["roi_t_max"] - feats["ref_t_mean"]
        feats["delta_t_mean"] = feats["roi_t_mean"] - feats["ref_t_mean"]
    if ambient_c is not None:
        feats["roi_minus_ambient"] = feats["roi_t_mean"] - ambient_c
    return feats
