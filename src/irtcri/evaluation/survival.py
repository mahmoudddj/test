"""Time-to-event analyses (Kaplan-Meier, log-rank, Cox PH with clustered SEs).

Implemented with numpy/scipy/statsmodels to keep the dependency footprint
small. Kaplan-Meier treats competing events (death, removal for other reasons)
as censoring - report this explicitly and consider cumulative-incidence
(Aalen-Johansen) sensitivity analyses for the final SAP.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def kaplan_meier(durations, events, alpha: float = 0.05) -> pd.DataFrame:
    d = np.asarray(durations, dtype=float)
    e = np.asarray(events, dtype=int)
    order = np.argsort(d)
    d, e = d[order], e[order]
    times = np.unique(d[e == 1])
    rows = [{"time": 0.0, "n_at_risk": len(d), "events": 0, "censored": 0, "survival": 1.0, "ci_low": 1.0,
             "ci_high": 1.0}]
    s, var_sum = 1.0, 0.0
    z = stats.norm.ppf(1 - alpha / 2)
    prev = 0.0
    for t in times:
        n = int((d >= t).sum())
        k = int(((d == t) & (e == 1)).sum())
        c = int(((d > prev) & (d < t) & (e == 0)).sum())
        s *= 1 - k / n
        if n - k > 0:
            var_sum += k / (n * (n - k))
        if 0 < s < 1:   # log(-log) CI (Kalbfleisch-Prentice)
            se = np.sqrt(var_sum) / abs(np.log(s))
            lo, hi = s ** np.exp(z * se), s ** np.exp(-z * se)
        else:
            lo = hi = s
        rows.append({"time": float(t), "n_at_risk": n, "events": k, "censored": c, "survival": s,
                     "ci_low": float(lo), "ci_high": float(hi)})
        prev = t
    return pd.DataFrame(rows)


def median_survival(km: pd.DataFrame) -> float:
    below = km[km["survival"] <= 0.5]
    return float(below["time"].iloc[0]) if len(below) else float("nan")


def logrank_test(durations, events, groups) -> dict:
    d = np.asarray(durations, float)
    e = np.asarray(events, int)
    g = np.asarray(groups)
    levels = np.unique(g)
    times = np.unique(d[e == 1])
    O = np.zeros(len(levels))
    E = np.zeros(len(levels))
    V = np.zeros((len(levels), len(levels)))
    for t in times:
        at_risk = d >= t
        n = at_risk.sum()
        k = ((d == t) & (e == 1)).sum()
        nj = np.array([(at_risk & (g == lv)).sum() for lv in levels])
        kj = np.array([((d == t) & (e == 1) & (g == lv)).sum() for lv in levels])
        O += kj
        E += k * nj / n
        if n > 1:
            V += (k * (n - k) / (n - 1)) * (np.diag(nj / n) - np.outer(nj / n, nj / n))
    df = len(levels) - 1
    diff = (O - E)[:df]
    chi2 = float(diff @ np.linalg.pinv(V[:df, :df]) @ diff)
    return {"chi2": chi2, "df": df, "p_value": float(stats.chi2.sf(chi2, df)),
            "observed": dict(zip(map(str, levels), O.tolist(), strict=True)),
            "expected": dict(zip(map(str, levels), E.tolist(), strict=True))}


def cox_ph(df: pd.DataFrame, duration_col: str, event_col: str, covariates: list[str],
           cluster_col: str | None = "patient_id") -> pd.DataFrame:
    """Cox proportional-hazards model; robust (sandwich) SEs clustered by patient when given."""
    from statsmodels.duration.hazard_regression import PHReg

    X = pd.get_dummies(df[covariates], drop_first=True, dtype=float)
    model = PHReg(df[duration_col].to_numpy(float), X.to_numpy(float), status=df[event_col].to_numpy(int),
                  ties="efron")
    res = model.fit(groups=df[cluster_col].to_numpy() if cluster_col else None)
    ci = np.asarray(res.conf_int())
    return pd.DataFrame({"term": X.columns, "hr": np.exp(res.params), "ci_low": np.exp(ci[:, 0]),
                         "ci_high": np.exp(ci[:, 1]), "p_value": res.pvalues,
                         "se_type": "cluster-robust" if cluster_col else "model"})
