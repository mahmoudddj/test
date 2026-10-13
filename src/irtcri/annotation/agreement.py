"""Inter-rater reliability for reader 1 vs reader 2 labels."""

from __future__ import annotations

import numpy as np
import pandas as pd

from irtcri.schemas.enums import POSITIVE_LABELS, InfectionLabel

# ordinal scale used for weighted kappa (UNCERTAIN excluded)
ORDINAL = {
    InfectionLabel.NO_INFECTION.value: 0, InfectionLabel.POSSIBLE_INFECTION.value: 1,
    InfectionLabel.EXIT_SITE_INFECTION.value: 2, InfectionLabel.POCKET_INFECTION.value: 2,
    InfectionLabel.TUNNEL_INFECTION.value: 3, InfectionLabel.CRBSI.value: 4,
}


def cohen_kappa(a: list, b: list, weights: str | None = None, categories: list | None = None) -> float:
    a, b = list(a), list(b)
    if len(a) != len(b) or not a:
        return float("nan")
    cats = categories or sorted(set(a) | set(b), key=str)
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    O = np.zeros((k, k))
    for x, y in zip(a, b, strict=True):
        O[idx[x], idx[y]] += 1
    O /= O.sum()
    E = np.outer(O.sum(1), O.sum(0))
    if weights is None:
        W = 1 - np.eye(k)
    else:
        vals = np.array([float(c) for c in cats])
        D = np.abs(vals[:, None] - vals[None, :])
        D = D / D.max() if D.max() > 0 else D
        W = D if weights == "linear" else D ** 2
    denom = (W * E).sum()
    if denom == 0:
        return 1.0
    return float(1 - (W * O).sum() / denom)


def agreement_report(pairs: pd.DataFrame, n_boot: int = 1000, seed: int = 0) -> dict:
    """``pairs`` columns: thermal_image_id, patient_id, label_r1, label_r2.

    Bootstrap CIs resample *patients* (images within a patient are correlated).
    """
    df = pairs.dropna(subset=["label_r1", "label_r2"]).copy()
    out: dict = {"n_images": int(len(df)), "n_patients": int(df["patient_id"].nunique()) if len(df) else 0}
    if df.empty:
        return out
    df["bin_r1"] = df["label_r1"].isin([x.value for x in POSITIVE_LABELS]).astype(int)
    df["bin_r2"] = df["label_r2"].isin([x.value for x in POSITIVE_LABELS]).astype(int)
    ordm = df[df["label_r1"].isin(ORDINAL) & df["label_r2"].isin(ORDINAL)]

    def stats(d: pd.DataFrame, o: pd.DataFrame) -> dict:
        return {
            "percent_agreement_categorical": float((d["label_r1"] == d["label_r2"]).mean()),
            "kappa_categorical": cohen_kappa(d["label_r1"], d["label_r2"]),
            "kappa_binary_infection": cohen_kappa(d["bin_r1"], d["bin_r2"], categories=[0, 1]),
            "weighted_kappa_quadratic_ordinal": cohen_kappa(
                o["label_r1"].map(ORDINAL), o["label_r2"].map(ORDINAL), weights="quadratic",
                categories=sorted(set(ORDINAL.values()))) if len(o) else float("nan"),
        }

    point = stats(df, ordm)
    rng = np.random.default_rng(seed)
    pats = df["patient_id"].unique()
    boots = {k: [] for k in point}
    groups = {p: g for p, g in df.groupby("patient_id")}
    for _ in range(n_boot):
        sample = rng.choice(pats, size=len(pats), replace=True)
        d = pd.concat([groups[p] for p in sample])
        o = d[d["label_r1"].isin(ORDINAL) & d["label_r2"].isin(ORDINAL)]
        for k, v in stats(d, o).items():
            boots[k].append(v)
    for k, v in point.items():
        arr = np.array(boots[k], dtype=float)
        arr = arr[np.isfinite(arr)]
        out[k] = {"estimate": v, "ci95": [float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))]
                  if arr.size else None}
    out["confusion"] = pd.crosstab(df["label_r1"], df["label_r2"]).to_dict()
    out["bootstrap"] = {"n": n_boot, "unit": "patient", "seed": seed}
    return out
