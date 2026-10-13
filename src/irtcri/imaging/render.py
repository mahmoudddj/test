"""Rendering helpers for viewers (PNG previews, temperature grids, overlays)."""

from __future__ import annotations

import io

import numpy as np
from matplotlib import colormaps
from PIL import Image


def thermal_png(t: np.ndarray, window: tuple[float, float] | None = None, cmap: str = "inferno",
                overlay: np.ndarray | None = None, overlay_alpha: float = 0.45) -> tuple[bytes, tuple[float, float]]:
    finite = t[np.isfinite(t)]
    lo, hi = window or ((float(np.percentile(finite, 1)), float(np.percentile(finite, 99.5)))
                        if finite.size else (20.0, 40.0))
    norm = np.clip((np.nan_to_num(t, nan=lo) - lo) / max(hi - lo, 1e-6), 0, 1)
    rgb = (colormaps[cmap](norm)[..., :3] * 255).astype(np.uint8)
    if overlay is not None:
        rgb = blend_heatmap(rgb, overlay, overlay_alpha)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    return buf.getvalue(), (lo, hi)


def blend_heatmap(rgb: np.ndarray, heat: np.ndarray, alpha: float = 0.45, cmap: str = "jet") -> np.ndarray:
    """Overlay a [0,1] heatmap (any size) on an RGB uint8 image."""
    h, w = rgb.shape[:2]
    hm = np.asarray(Image.fromarray((np.clip(heat, 0, 1) * 255).astype(np.uint8)).resize((w, h),
                                                                                         Image.Resampling.BILINEAR))
    col = (colormaps[cmap](hm / 255.0)[..., :3] * 255).astype(np.float32)
    a = alpha * (hm[..., None] / 255.0)
    return (rgb.astype(np.float32) * (1 - a) + col * a).astype(np.uint8)


def palette_png(pixels: np.ndarray, max_side: int | None = None) -> bytes:
    img = Image.fromarray(pixels)
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def downsample_grid(t: np.ndarray, max_side: int = 160) -> dict:
    step = max(1, int(np.ceil(max(t.shape) / max_side)))
    g = t[::step, ::step]
    return {"step": step, "height": int(g.shape[0]), "width": int(g.shape[1]),
            "values": [[None if not np.isfinite(v) else round(float(v), 2) for v in row] for row in g]}
