"""Feature attributions for clinical predictors.

* logistic regression: exact additive contributions on the log-odds scale
  (coef x standardised value) - equivalent to linear SHAP with a mean baseline;
* any model + ``shap`` installed: SHAP values (``pip install irtcri[explain]``);
* otherwise: occlusion attribution (replace a feature by its training median and
  record the change in predicted probability). Labelled as such - NOT SHAP.

Attributions describe the model, not the patient's pathophysiology.
"""

from __future__ import annotations

from typing import Callable

import numpy as np


def linear_contributions(coef: np.ndarray, x_std: np.ndarray, names: list[str], top: int = 10) -> dict:
    c = coef.ravel() * x_std.ravel()
    order = np.argsort(-np.abs(c))[:top]
    return {"method": "linear_logodds_contribution", "unit": "log-odds",
            "contributions": {names[i]: float(c[i]) for i in order}}


def occlusion_contributions(predict: Callable[[np.ndarray], np.ndarray], x: np.ndarray, baseline: np.ndarray,
                            names: list[str], top: int = 10) -> dict:
    x = x.reshape(1, -1).astype(np.float32)
    p0 = float(predict(x)[0])
    X = np.repeat(x, x.shape[1], axis=0)
    X[np.arange(x.shape[1]), np.arange(x.shape[1])] = baseline.ravel()
    delta = p0 - predict(X)
    order = np.argsort(-np.abs(delta))[:top]
    return {"method": "occlusion_to_training_median", "unit": "probability",
            "contributions": {names[i]: float(delta[i]) for i in order}, "prediction": p0}


def shap_contributions(predict: Callable[[np.ndarray], np.ndarray], x: np.ndarray, background: np.ndarray,
                       names: list[str], top: int = 10) -> dict | None:
    try:
        import shap
    except ImportError:
        return None
    expl = shap.Explainer(predict, background, feature_names=names)
    v = expl(x.reshape(1, -1)).values[0]
    order = np.argsort(-np.abs(v))[:top]
    return {"method": "shap", "unit": "probability", "contributions": {names[i]: float(v[i]) for i in order}}
