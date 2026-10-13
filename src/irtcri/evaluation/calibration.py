"""Calibration assessment."""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def calibration_curve(y, p, n_bins: int = 10, strategy: str = "quantile") -> pd.DataFrame:
    y = np.asarray(y).astype(float)
    p = np.asarray(p, dtype=float)
    if strategy == "quantile":
        edges = np.unique(np.quantile(p, np.linspace(0, 1, n_bins + 1)))
    else:
        edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=True), 0, len(edges) - 2)
    rows = []
    for b in range(len(edges) - 1):
        m = idx == b
        if m.sum() == 0:
            continue
        rows.append({"bin": b, "n": int(m.sum()), "mean_predicted": float(p[m].mean()),
                     "observed_rate": float(y[m].mean())})
    return pd.DataFrame(rows)


def calibration_intercept_slope(y, p) -> dict[str, float]:
    """Calibration slope (logistic regression of y on logit p) and calibration-in-the-large
    (intercept with logit p as offset). Ideal: slope 1, intercept 0."""
    y = np.asarray(y).astype(float)
    lp = _logit(p)
    out = {"slope": float("nan"), "intercept_citl": float("nan")}
    if len(np.unique(y)) < 2:
        return out
    try:
        m = sm.GLM(y, sm.add_constant(lp), family=sm.families.Binomial()).fit()
        out["slope"] = float(m.params[1])
        out["slope_ci"] = [float(x) for x in m.conf_int()[1]]
        m0 = sm.GLM(y, np.ones_like(lp), family=sm.families.Binomial(), offset=lp).fit()
        out["intercept_citl"] = float(m0.params[0])
        out["intercept_ci"] = [float(x) for x in m0.conf_int()[0]]
    except Exception as e:  # perfect separation etc.
        out["error"] = str(e)
    return out


def expected_calibration_error(y, p, n_bins: int = 10) -> float:
    c = calibration_curve(y, p, n_bins, "uniform")
    if c.empty:
        return float("nan")
    return float((c["n"] * (c["mean_predicted"] - c["observed_rate"]).abs()).sum() / c["n"].sum())


class PlattCalibrator:
    """Logistic recalibration fitted on the validation set only."""

    def fit(self, y, p) -> "PlattCalibrator":
        m = sm.GLM(np.asarray(y, float), sm.add_constant(_logit(p)), family=sm.families.Binomial()).fit()
        self.a, self.b = float(m.params[0]), float(m.params[1])
        return self

    def transform(self, p) -> np.ndarray:
        return 1 / (1 + np.exp(-(self.a + self.b * _logit(p))))

    def to_dict(self) -> dict:
        return {"method": "platt", "a": self.a, "b": self.b}
