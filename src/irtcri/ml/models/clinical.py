"""Model family 0: clinical-data-only baselines (scikit-learn)."""

from __future__ import annotations

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression


def make_clinical_estimator(kind: str, seed: int, class_weight: str | None = "balanced"):
    if kind == "logistic":
        return LogisticRegression(C=0.5, class_weight=class_weight, max_iter=2000, random_state=seed)
    if kind == "hgb":
        return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, l2_regularization=1.0,
                                              class_weight=class_weight, random_state=seed)
    raise ValueError(kind)
