"""Patient-level data partitioning (no patient may appear in more than one split)."""

from __future__ import annotations

import json
from typing import Iterable

import numpy as np
import pandas as pd

from irtcri.core.provenance import sha256_json


class PatientLeakageError(AssertionError):
    pass


def patient_level_split(patients: pd.DataFrame, seed: int, test_fraction: float = 0.2, val_fraction: float = 0.2,
                        stratify: Iterable[str] = ("has_event", "site_id"),
                        holdout_sites: Iterable[str] = (), order_key: str = "study_id") -> pd.Series:
    """Assign each patient to train/val/test (or external_test for held-out sites).

    Stratified on patient-level event status and site so prevalence and site mix are similar
    across splits. Deterministic for a given seed, patient set and ``order_key`` (study_id), so a
    rebuild from a re-imported database reproduces the same split.
    """
    p = patients.drop_duplicates("patient_id").set_index("patient_id")
    rng = np.random.default_rng(seed)
    assign = pd.Series("train", index=p.index, dtype=object)
    holdout_sites = set(holdout_sites)
    if holdout_sites:
        assign[p["site_id"].isin(holdout_sites)] = "external_test"
    dev = p[~p["site_id"].isin(holdout_sites)]
    strat_cols = [c for c in stratify if c in dev.columns]
    groups = dev.groupby(strat_cols, dropna=False) if strat_cols else [((), dev)]
    for _, g in groups:
        # order by a stable key (pseudonymous study_id when given) - never by random internal UUIDs
        ids = np.array(g.sort_values(order_key).index if order_key in g.columns else sorted(g.index))
        rng.shuffle(ids)
        n = len(ids)
        n_test = int(round(n * test_fraction))
        n_val = int(round(n * val_fraction))
        if n >= 3 and test_fraction > 0 and n_test == 0:
            n_test = 1
        assign[ids[:n_test]] = "test"
        assign[ids[n_test:n_test + n_val]] = "val"
    return assign.rename("split")


def patient_kfold(patients: pd.DataFrame, k: int, seed: int, stratify: str = "has_event") -> pd.Series:
    """Fold index per patient for cross-validation within the development set."""
    p = patients.drop_duplicates("patient_id").set_index("patient_id")
    rng = np.random.default_rng(seed)
    fold = pd.Series(-1, index=p.index)
    for _, g in p.groupby(stratify) if stratify in p else [((), p)]:
        ids = np.array(sorted(g.index))
        rng.shuffle(ids)
        for i, pid in enumerate(ids):
            fold[pid] = i % k
    return fold.rename("fold")


def assert_no_patient_leakage(df: pd.DataFrame, split_col: str = "split", group_col: str = "patient_id") -> None:
    n = df.groupby(group_col)[split_col].nunique()
    bad = n[n > 1]
    if len(bad):
        raise PatientLeakageError(f"{len(bad)} patients appear in more than one split: {list(bad.index[:5])}")


def split_fingerprint(assign: pd.Series) -> str:
    return sha256_json(json.loads(assign.sort_index().to_json()))
