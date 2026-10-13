"""Temporal utilities for longitudinal clinical data.

Core rule: a value may be used for a prediction at time ``t`` only if its
``available_time <= t`` (see ``schemas.dictionary.availability_time``). Every
feature function records the latest availability time it used, so the
dataset builder can *prove* the absence of future leakage
(``assert_no_future_leakage``).
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from irtcri.schemas.dictionary import load_dictionary

AGGREGATIONS = ("last", "mean", "min", "max", "count", "slope", "time_since_last_h")


class FutureLeakageError(AssertionError):
    pass


def unify_measurements(obs: pd.DataFrame, labs: pd.DataFrame) -> pd.DataFrame:
    cols = ["id", "patient_id", "episode_id", "variable", "value_num", "event_time", "source_time", "recorded_time"]
    frames = [d[cols].assign(table=name) for name, d in (("observation", obs), ("laboratory", labs)) if len(d)]
    if not frames:
        return pd.DataFrame(columns=cols + ["table", "available_time"])
    m = pd.concat(frames, ignore_index=True)
    m = m[m["value_num"].notna()].copy()
    return add_available_time(m)


def add_available_time(m: pd.DataFrame) -> pd.DataFrame:
    d = load_dictionary()
    m = m.copy()
    lag = m["variable"].map({v.name: v.availability.lag_minutes for v in d.variables}).fillna(0)
    basis = m["variable"].map({v.name: v.availability.basis for v in d.variables}).fillna("event_time")
    m["event_time"] = pd.to_datetime(m["event_time"], utc=True)
    m["source_time"] = pd.to_datetime(m["source_time"], utc=True)
    event_plus_lag = m["event_time"] + pd.to_timedelta(lag, unit="m")
    use_source = (basis == "source_time") & m["source_time"].notna()
    reported = m["source_time"].where(m["source_time"] > m["event_time"], m["event_time"])
    m["available_time"] = event_plus_lag.where(~use_source, reported)
    return m


def window_features(samples: pd.DataFrame, meas: pd.DataFrame, variables: Iterable[str], lookback_h: float,
                    aggregations: Iterable[str] = ("last", "mean", "min", "max", "count", "slope",
                                                   "time_since_last_h"),
                    time_col: str = "prediction_time", key: str = "patient_id") -> pd.DataFrame:
    """Aggregate each variable over [t - lookback, t], using only values available by t.

    Returns one row per sample index with columns ``<var>__<agg>`` plus
    ``_max_available_time`` (latest availability time actually used).
    """
    variables = list(variables)
    aggregations = list(aggregations)
    out = pd.DataFrame(index=samples.index)
    out["_max_available_time"] = pd.NaT
    out["_max_available_time"] = pd.to_datetime(out["_max_available_time"], utc=True)
    if meas.empty or samples.empty:
        for v in variables:
            for a in aggregations:
                out[f"{v}__{a}"] = 0.0 if a == "count" else np.nan
        return out
    s = samples[[key, time_col]].reset_index(names="_sidx")
    m = meas[meas["variable"].isin(variables)][[key, "variable", "value_num", "event_time", "available_time"]]
    j = s.merge(m, on=key, how="inner")
    lb = pd.Timedelta(hours=lookback_h)
    j = j[(j["available_time"] <= j[time_col]) & (j["event_time"] >= j[time_col] - lb)
          & (j["event_time"] <= j[time_col])]
    j = j.sort_values(["_sidx", "variable", "event_time"])
    j["_hours"] = (j["event_time"] - j[time_col]).dt.total_seconds() / 3600
    g = j.groupby(["_sidx", "variable"])
    res = {}
    if "last" in aggregations:
        res["last"] = g["value_num"].last()
    if "mean" in aggregations:
        res["mean"] = g["value_num"].mean()
    if "min" in aggregations:
        res["min"] = g["value_num"].min()
    if "max" in aggregations:
        res["max"] = g["value_num"].max()
    if "count" in aggregations:
        res["count"] = g["value_num"].count()
    if "time_since_last_h" in aggregations:
        res["time_since_last_h"] = -g["_hours"].max()
    if "slope" in aggregations:
        def _slope(d: pd.DataFrame) -> float:
            if len(d) < 2 or d["_hours"].nunique() < 2:
                return np.nan
            return float(np.polyfit(d["_hours"], d["value_num"], 1)[0])
        res["slope"] = g[["_hours", "value_num"]].apply(_slope)
    for agg, ser in res.items():
        wide = ser.unstack("variable")
        for v in variables:
            col = f"{v}__{agg}"
            out[col] = wide[v].reindex(out.index) if v in wide.columns else np.nan
            if agg == "count":
                out[col] = out[col].fillna(0)
    mx = j.groupby("_sidx")["available_time"].max()
    out.loc[mx.index, "_max_available_time"] = mx
    return out


def nearest_prior(samples: pd.DataFrame, meas: pd.DataFrame, variable: str, tolerance_h: float | None = None,
                  time_col: str = "prediction_time", key: str = "patient_id") -> pd.Series:
    """Most recent value of ``variable`` available at prediction time (as-of join)."""
    m = meas[meas["variable"] == variable][[key, "available_time", "value_num"]].sort_values("available_time")
    s = samples[[key, time_col]].reset_index(names="_sidx").sort_values(time_col)
    if m.empty:
        return pd.Series(np.nan, index=samples.index)
    j = pd.merge_asof(s, m, left_on=time_col, right_on="available_time", by=key, direction="backward",
                      tolerance=pd.Timedelta(hours=tolerance_h) if tolerance_h else None)
    return j.set_index("_sidx")["value_num"].reindex(samples.index)


def missing_indicators(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    return pd.DataFrame({f"{c}__missing": df[c].isna().astype(np.int8) for c in columns}, index=df.index)


def resample_regular(meas: pd.DataFrame, variable: str, freq_h: float, start: pd.Timestamp, end: pd.Timestamp,
                     max_carry_h: float | None = None) -> pd.DataFrame:
    """Regular grid (for descriptive/statistical analyses) with bounded last-observation-carried-forward.

    Uses availability time, so the grid is also leakage-safe."""
    grid = pd.date_range(start, end, freq=pd.Timedelta(hours=freq_h))
    m = meas[meas["variable"] == variable].sort_values("available_time")
    g = pd.DataFrame({"grid_time": grid})
    if m.empty:
        g["value"] = np.nan
        return g
    j = pd.merge_asof(g, m[["available_time", "value_num"]], left_on="grid_time", right_on="available_time",
                      direction="backward",
                      tolerance=pd.Timedelta(hours=max_carry_h) if max_carry_h else None)
    return j.rename(columns={"value_num": "value"})[["grid_time", "value", "available_time"]]


def assert_no_future_leakage(samples: pd.DataFrame, time_col: str = "prediction_time",
                             avail_cols: Iterable[str] = ("_max_available_time",)) -> None:
    for c in avail_cols:
        if c not in samples:
            continue
        bad = samples[samples[c].notna() & (samples[c] > samples[time_col])]
        if len(bad):
            raise FutureLeakageError(f"{len(bad)} samples use information from after prediction time (column {c})")
