"""Stratified / subgroup analyses with explicit pre-specification labels."""

from __future__ import annotations

from typing import Callable

import pandas as pd


def stratified(df: pd.DataFrame, by: str, fn: Callable[[pd.DataFrame], dict], analysis_type: str = "secondary",
               min_events: int = 1) -> pd.DataFrame:
    """Apply ``fn`` within each level of ``by``. ``analysis_type``: primary|secondary|exploratory."""
    rows = []
    for level, g in df.groupby(by, dropna=False):
        n_events = int(g["label"].sum()) if "label" in g else None
        r = {"subgroup_variable": by, "level": level, "analysis_type": analysis_type, "n": len(g),
             "n_patients": g["patient_id"].nunique() if "patient_id" in g else None, "n_events": n_events}
        if n_events is not None and n_events < min_events:
            r["note"] = "too few events - not estimated"
        else:
            r.update(fn(g))
        rows.append(r)
    out = pd.DataFrame(rows)
    if analysis_type == "exploratory":
        out["caveat"] = "EXPLORATORY - hypothesis generating, not adjusted for multiplicity"
    return out
