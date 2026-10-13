"""Outcome / label construction for image-time-point samples and episodes."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LabelConfig:
    # early_warning: positive when the conventional clinical recognition time R lies within
    #                [t, t + horizon]  (i.e. the image precedes recognition by <= horizon).
    # detection:     positive when the adjudicated onset <= t <= R + grace.
    mode: str = "early_warning"
    horizon_hours: float = 48.0
    grace_hours: float = 0.0
    reference: str = "clinical_recognition_time"
    pre_horizon_policy: str = "negative"        # negative | exclude (event episodes, t < R - horizon)
    include_post_recognition: bool = False      # samples after R are normally excluded (already treated)
    require_adjudicated: bool = False


def label_samples(samples: pd.DataFrame, outcomes: pd.DataFrame, cfg: LabelConfig) -> pd.DataFrame:
    """Adds ``label`` (0/1), ``label_status`` (included/excluded_*), ``hours_to_recognition``."""
    oc = outcomes[["episode_id", "is_event", "clinical_recognition_time", "onset_time_reference", "status",
                   "outcome_type"]]
    s = samples.merge(oc, on="episode_id", how="left")
    s["label"] = np.nan
    s["label_status"] = "included"
    s.loc[s["is_event"].isna(), "label_status"] = "excluded_no_outcome"
    if cfg.require_adjudicated:
        s.loc[s["is_event"].notna() & (s["status"] != "adjudicated"), "label_status"] = "excluded_not_adjudicated"
    ev = s["is_event"].fillna(False).astype(bool)
    R = s["clinical_recognition_time"]
    t = s["prediction_time"]
    s["hours_to_recognition"] = (R - t).dt.total_seconds() / 3600
    s.loc[~ev & s["is_event"].notna(), "label"] = 0
    if cfg.mode == "early_warning":
        h = s["hours_to_recognition"]
        pos = ev & (h >= 0) & (h <= cfg.horizon_hours)
        pre = ev & (h > cfg.horizon_hours)
        post = ev & (h < 0)
        s.loc[pos, "label"] = 1
        if cfg.pre_horizon_policy == "negative":
            s.loc[pre, "label"] = 0
        else:
            s.loc[pre, "label_status"] = "excluded_pre_horizon"
    elif cfg.mode == "detection":
        onset = s["onset_time_reference"].fillna(R)
        upper = R + pd.Timedelta(hours=cfg.grace_hours)
        s.loc[ev & (t >= onset) & (t <= upper), "label"] = 1
        s.loc[ev & (t < onset), "label"] = 0
        post = ev & (t > upper)
    else:
        raise ValueError(f"unknown label mode {cfg.mode}")
    if not cfg.include_post_recognition:
        s.loc[post & (s["label_status"] == "included"), "label_status"] = "excluded_post_recognition"
    elif cfg.mode == "early_warning":
        s.loc[post, "label"] = 1
    s.loc[s["label_status"] != "included", "label"] = np.nan
    return s


def time_to_event(episodes: pd.DataFrame, outcomes: pd.DataFrame, now: pd.Timestamp,
                  reference: str = "clinical_recognition_time") -> pd.DataFrame:
    """Episode-level survival data: time from follow-up start to infection recognition, else censoring.

    Censoring at catheter removal, end of follow-up or data cut-off. Death / removal for other
    reasons are competing events; flagged in ``censor_reason`` for competing-risk sensitivity analyses.
    """
    e = episodes.merge(outcomes[["episode_id", "is_event", reference, "outcome_type", "death"]],
                       left_on="id", right_on="episode_id", how="left")
    end = e["removal_time"].combine_first(e["end_of_follow_up_time"]).fillna(now).clip(upper=now)
    is_ev = e["is_event"].fillna(False).astype(bool) & e[reference].notna()
    stop = end.where(~is_ev, e[reference])
    out = pd.DataFrame({
        "episode_id": e["id"], "patient_id": e["patient_id"], "site_id": e["site_id"],
        "catheter_type": e["catheter_type"], "start_time": e["follow_up_start_time"], "stop_time": stop,
        "event": is_ev.astype(int),
    })
    out["duration_h"] = (out["stop_time"] - out["start_time"]).dt.total_seconds() / 3600
    reason = np.where(is_ev, "event", np.where(e["removal_time"].notna(), "catheter_removed",
                      np.where(e["end_of_follow_up_time"].notna(), "end_of_follow_up", "administrative_cutoff")))
    reason = np.where((~is_ev) & e["death"].fillna(False).astype(bool), "death", reason)
    out["censor_reason"] = reason
    out["outcome_known"] = e["is_event"].notna()
    return out
