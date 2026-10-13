"""Publication-quality figures (PNG 300 dpi + PDF)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import precision_recall_curve, roc_curve  # noqa: E402

from irtcri.evaluation.calibration import calibration_curve  # noqa: E402

plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 100,
                     "savefig.dpi": 300, "font.family": "DejaVu Sans"})


def _save(fig, path: Path) -> list[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for ext in (".png", ".pdf"):
        fig.savefig(path.with_suffix(ext), bbox_inches="tight")
        out.append(str(path.with_suffix(ext)))
    plt.close(fig)
    return out


def roc_pr(y, p, path: Path, label: str = "model", threshold: float | None = None) -> list[str]:
    fig, ax = plt.subplots(1, 2, figsize=(7, 3.3))
    fpr, tpr, thr = roc_curve(y, p)
    ax[0].plot(fpr, tpr, color="#1f4e79", lw=1.5, label=label)
    ax[0].plot([0, 1], [0, 1], ls=":", color="grey", lw=1)
    ax[0].set(xlabel="1 - specificity", ylabel="Sensitivity", title="ROC")
    prec, rec, _ = precision_recall_curve(y, p)
    ax[1].plot(rec, prec, color="#1f4e79", lw=1.5)
    ax[1].axhline(np.mean(y), ls=":", color="grey", lw=1, label="prevalence")
    ax[1].set(xlabel="Recall (sensitivity)", ylabel="Precision (PPV)", title="Precision-recall", ylim=(0, 1.02))
    if threshold is not None:
        yb, pb = np.asarray(y), np.asarray(p) >= threshold
        tp, fp = (pb & (yb == 1)).sum(), (pb & (yb == 0)).sum()
        fn, tn = (~pb & (yb == 1)).sum(), (~pb & (yb == 0)).sum()
        ax[0].plot(fp / max(fp + tn, 1), tp / max(tp + fn, 1), "o", color="#c00000", ms=5, label="operating point")
        ax[1].plot(tp / max(tp + fn, 1), tp / max(tp + fp, 1), "o", color="#c00000", ms=5)
    ax[0].legend(frameon=False, loc="lower right")
    return _save(fig, path)


def calibration_plot(y, p, path: Path, n_bins: int = 10) -> list[str]:
    c = calibration_curve(y, p, n_bins)
    fig, ax = plt.subplots(figsize=(3.4, 3.4))
    ax.plot([0, 1], [0, 1], ls=":", color="grey", lw=1)
    ax.plot(c["mean_predicted"], c["observed_rate"], "o-", color="#1f4e79", ms=4)
    ax.set(xlabel="Predicted probability", ylabel="Observed proportion", title="Calibration", xlim=(0, 1), ylim=(0, 1))
    return _save(fig, path)


def lead_time_histogram(lead_h: pd.Series, path: Path, target_h: float = 6.0) -> list[str]:
    fig, ax = plt.subplots(figsize=(4.2, 3))
    if len(lead_h):
        ax.hist(lead_h, bins=min(20, max(5, len(lead_h))), color="#1f4e79", alpha=.85)
    ax.axvline(0, color="black", lw=1)
    ax.axvline(target_h, color="#c00000", ls="--", lw=1, label=f"{target_h:g} h target")
    ax.set(xlabel="Lead time vs conventional recognition (h)", ylabel="Detected events",
           title="Warning lead time")
    ax.legend(frameon=False)
    return _save(fig, path)


def km_plot(curves: dict[str, pd.DataFrame], path: Path, xlabel: str = "Hours since follow-up start") -> list[str]:
    fig, ax = plt.subplots(figsize=(4.5, 3.3))
    for name, km in curves.items():
        ax.step(km["time"], 1 - km["survival"], where="post", label=name, lw=1.4)
        ax.fill_between(km["time"], 1 - km["ci_low"], 1 - km["ci_high"], step="post", alpha=.15)
    ax.set(xlabel=xlabel, ylabel="Cumulative incidence (1 - KM)", title="Time to CRI/ESI recognition", ylim=(0, None))
    ax.legend(frameon=False)
    return _save(fig, path)
