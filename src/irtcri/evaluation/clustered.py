"""Regression models appropriate for repeated measurements.

* ``gee_logistic`` - population-averaged odds ratios for repeated binary
  outcomes (e.g. image-level label vs thermal delta-T), exchangeable working
  correlation within patient, robust SEs.
* ``mixed_linear`` - linear mixed model with patient random intercepts (and
  optional random slopes) for continuous repeated measures (e.g. delta-T
  trajectories before recognition).
* ``logistic`` - ordinary logistic regression, ONLY for one-row-per-unit data
  (e.g. episode-level analyses).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf


def _or_table(res, robust_note: str) -> pd.DataFrame:
    ci = res.conf_int()
    return pd.DataFrame({"term": res.params.index, "odds_ratio": np.exp(res.params.values),
                         "ci_low": np.exp(ci[0].values), "ci_high": np.exp(ci[1].values),
                         "p_value": res.pvalues.values, "note": robust_note})


def gee_logistic(df: pd.DataFrame, formula: str, groups: str = "patient_id") -> pd.DataFrame:
    d = df.sort_values(groups)
    model = smf.gee(formula, groups, data=d, family=sm.families.Binomial(),
                    cov_struct=sm.cov_struct.Exchangeable())
    return _or_table(model.fit(), "GEE exchangeable, robust SE")


def mixed_linear(df: pd.DataFrame, formula: str, groups: str = "patient_id", re_formula: str | None = None
                 ) -> pd.DataFrame:
    model = smf.mixedlm(formula, df, groups=df[groups], re_formula=re_formula)
    res = model.fit(method="lbfgs")
    ci = res.conf_int()
    fe = res.fe_params.index
    return pd.DataFrame({"term": fe, "estimate": res.fe_params.values, "ci_low": ci.loc[fe, 0].values,
                         "ci_high": ci.loc[fe, 1].values, "p_value": res.pvalues[fe].values,
                         "note": f"random effects: {re_formula or '1'} | {groups}"})


def logistic(df: pd.DataFrame, formula: str, unit_col: str | None = None) -> pd.DataFrame:
    if unit_col is not None and df[unit_col].duplicated().any():
        raise ValueError(f"logistic(): repeated rows per {unit_col}; use gee_logistic for clustered data")
    res = smf.logit(formula, df).fit(disp=0)
    return _or_table(res, "maximum likelihood (independent units)")
