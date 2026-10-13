"""RGB <-> thermal registration.

MVP approach (robust, auditable): an affine transform estimated by least
squares from >= 3 manually clicked control-point pairs (or fixed per camera
model when the vendor documents the optical offset). Automatic multimodal
registration (e.g. mutual-information) can be added later; its benefit must be
demonstrated on study data before it is used for modelling.
"""

from __future__ import annotations

import numpy as np
from PIL import Image


def estimate_affine(src_pts: list[list[float]], dst_pts: list[list[float]]) -> tuple[np.ndarray, float]:
    """Return 2x3 affine mapping src (RGB pixels) -> dst (thermal pixels) and RMS error in dst pixels."""
    src = np.asarray(src_pts, dtype=float)
    dst = np.asarray(dst_pts, dtype=float)
    if src.shape != dst.shape or src.shape[0] < 3 or src.shape[1] != 2:
        raise ValueError("need >= 3 matching (x, y) control points")
    A = np.hstack([src, np.ones((len(src), 1))])
    M, *_ = np.linalg.lstsq(A, dst, rcond=None)      # 3x2
    pred = A @ M
    rms = float(np.sqrt(np.mean(np.sum((pred - dst) ** 2, axis=1))))
    return M.T, rms                                   # 2x3


def warp_rgb_to_thermal(rgb: np.ndarray, affine_2x3: np.ndarray, thermal_shape: tuple[int, int]) -> np.ndarray:
    """Resample the RGB image onto the thermal pixel grid."""
    M = np.vstack([np.asarray(affine_2x3, dtype=float), [0, 0, 1]])
    inv = np.linalg.inv(M)   # PIL wants dst -> src mapping
    h, w = thermal_shape
    img = Image.fromarray(rgb).transform((w, h), Image.Transform.AFFINE, data=tuple(inv[:2].flatten()),
                                         resample=Image.Resampling.BILINEAR)
    return np.asarray(img)
