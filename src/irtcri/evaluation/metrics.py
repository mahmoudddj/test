"""Binary classification metrics with patient-clustered bootstrap confidence intervals.

Repeated images of the same patient are NOT independent. All CIs here are
obtained by resampling *clusters* (patients by default), never individual images.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import average_precision_score, roc_auc_score


def confusion(y: np.ndarray, p: np.ndarray, thr: float) -> dict[str, int]:
    y = np.asarray(y).astype(int)
    pred = (np.asarray(p) >= thr).astype(int)
    return {"tp": int(((pred == 1) & (y == 1)).sum()), "fp": int(((pred == 1) & (y == 0)).sum()),
            "tn": int(((pred == 0) & (y == 0)).sum()), "fn": int(((pred == 0) & (y == 1)).sum())}


def _div(a: float, b: float) -> float:
    return float(a / b) if b else float("nan")


def binary_metrics(y, p, thr: float) -> dict[str, float]:
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    c = confusion(y, p, thr)
    tp, fp, tn, fn = c["tp"], c["fp"], c["tn"], c["fn"]
    sens, spec = _div(tp, tp + fn), _div(tn, tn + fp)
    ppv, npv = _div(tp, tp + fp), _div(tn, tn + fn)
    f1 = _div(2 * tp, 2 * tp + fp + fn)
    two_classes = len(np.unique(y)) == 2
    return {
        "n": int(len(y)), "prevalence": float(y.mean()) if len(y) else float("nan"), **c,
        "sensitivity": sens, "specificity": spec, "ppv": ppv, "npv": npv, "precision": ppv, "recall": sens,
        "f1": f1, "accuracy": _div(tp + tn, len(y)), "balanced_accuracy": np.nanmean([sens, spec]),
        "auroc": float(roc_auc_score(y, p)) if two_classes else float("nan"),
        "auprc": float(average_precision_score(y, p)) if two_classes else float("nan"),
        "brier": float(np.mean((p - y) ** 2)) if len(y) else float("nan"),
        "threshold": float(thr),
    }


def wilson_ci(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score interval (for proportions from *independent* units, e.g. one per patient)."""
    if n == 0:
        return (float("nan"), float("nan"))
    z = stats.norm.ppf(1 - alpha / 2)
    phat = k / n
    denom = 1 + z ** 2 / n
    centre = (phat + z ** 2 / (2 * n)) / denom
    half = z * np.sqrt(phat * (1 - phat) / n + z ** 2 / (4 * n ** 2)) / denom
    return (float(centre - half), float(centre + half))


def cluster_bootstrap(df: pd.DataFrame, fn: Callable[[pd.DataFrame], dict[str, float]], cluster_col: str = "patient_id",
                      n_boot: int = 2000, seed: int = 0, alpha: float = 0.05,
                      strata_col: str | None = None) -> dict[str, dict]:
    """Percentile bootstrap resampling clusters with replacement (optionally within strata, e.g. site)."""
    point = fn(df)
    rng = np.random.default_rng(seed)
    groups = {k: g for k, g in df.groupby(cluster_col)}
    if strata_col:
        strata = df.groupby(strata_col)[cluster_col].unique().to_dict()
    else:
        strata = {None: np.array(list(groups))}
    samples: dict[str, list[float]] = {k: [] for k in point}
    for _ in range(n_boot):
        ids = np.concatenate([rng.choice(v, size=len(v), replace=True) for v in strata.values()])
        d = pd.concat([groups[i] for i in ids], ignore_index=True)
        try:
            r = fn(d)
        except ValueError:
            continue
        for k in point:
            samples[k].append(r.get(k, np.nan))
    out = {}
    for k, v in point.items():
        arr = np.asarray(samples[k], dtype=float)
        arr = arr[np.isfinite(arr)]
        if isinstance(v, (int, float, np.floating)) and arr.size >= max(20, n_boot // 10):
            lo, hi = np.percentile(arr, [100 * alpha / 2, 100 * (1 - alpha / 2)])
            out[k] = {"estimate": float(v), "ci_low": float(lo), "ci_high": float(hi), "n_boot_valid": int(arr.size)}
        else:
            out[k] = {"estimate": float(v) if isinstance(v, (int, float, np.floating)) else v,
                      "ci_low": None, "ci_high": None, "n_boot_valid": int(arr.size)}
    return out


def metrics_with_ci(df: pd.DataFrame, thr: float, y_col: str = "label", p_col: str = "prob",
                    cluster_col: str = "patient_id", n_boot: int = 2000, seed: int = 0) -> dict[str, dict]:
    keys = ("sensitivity", "specificity", "ppv", "npv", "f1", "auroc", "auprc", "brier", "balanced_accuracy")

    def fn(d: pd.DataFrame) -> dict[str, float]:
        m = binary_metrics(d[y_col], d[p_col], thr)
        return {k: m[k] for k in keys}

    res = cluster_bootstrap(df, fn, cluster_col, n_boot, seed)
    base = binary_metrics(df[y_col], df[p_col], thr)
    res["counts"] = {k: base[k] for k in ("n", "tp", "fp", "tn", "fn", "prevalence")}
    res["counts"]["n_clusters"] = int(df[cluster_col].nunique())
    return res


def threshold_table(y, p, thresholds=None) -> pd.DataFrame:
    thresholds = np.round(np.arange(0.05, 0.96, 0.05), 2) if thresholds is None else thresholds
    rows = []
    for t in thresholds:
        m = binary_metrics(y, p, t)
        rows.append({k: m[k] for k in ("threshold", "sensitivity", "specificity", "ppv", "npv", "f1", "tp", "fp",
                                       "tn", "fn")})
    return pd.DataFrame(rows)


def patient_level(df: pd.DataFrame, y_col: str = "label", p_col: str = "prob", group: str = "patient_id",
                  agg: str = "max") -> pd.DataFrame:
    """Collapse to one row per patient (or episode): label = any positive, score = max (or mean) probability."""
    g = df.groupby(group)
    return pd.DataFrame({y_col: g[y_col].max(), p_col: g[p_col].agg(agg)}).reset_index()
