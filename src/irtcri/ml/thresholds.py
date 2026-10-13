"""Operating-threshold selection - always on validation data, never on the test set."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import roc_curve

from irtcri.evaluation.metrics import binary_metrics


def select_threshold(y, p, rule: str = "target_sensitivity", target: float = 0.85) -> tuple[float, dict]:
    y = np.asarray(y).astype(int)
    p = np.asarray(p, float)
    if rule.startswith("fixed"):
        thr = float(target)
    elif len(np.unique(y)) < 2:
        thr = 0.5
    elif rule == "youden":
        fpr, tpr, t = roc_curve(y, p)
        thr = float(t[np.argmax(tpr - fpr)])
    elif rule == "max_f1":
        cands = np.unique(p)
        f1 = [binary_metrics(y, p, c)["f1"] for c in cands]
        thr = float(cands[int(np.nanargmax(f1))])
    elif rule == "target_sensitivity":
        # highest threshold that still reaches the target sensitivity on validation data
        cands = np.sort(np.unique(p))[::-1]
        thr = float(cands[-1])
        for c in cands:
            if binary_metrics(y, p, c)["sensitivity"] >= target:
                thr = float(c)
                break
    elif rule == "target_precision":
        cands = np.sort(np.unique(p))
        thr = float(cands[-1])
        for c in cands:
            if binary_metrics(y, p, c)["ppv"] >= target:
                thr = float(c)
                break
    else:
        raise ValueError(f"unknown threshold rule {rule}")
    thr = float(np.clip(thr, 1e-6, 1 - 1e-6))
    return thr, {"rule": rule, "target": target, "selected_on": "validation",
                 "validation_metrics_at_threshold": binary_metrics(y, p, thr)}
