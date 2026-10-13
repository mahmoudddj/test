"""Event-based early-warning evaluation (the primary 'time to early detection' concept).

Unit of analysis = catheter episode, not image. For each episode, the model's
predictions over time are turned into ALERTS with an explicit, pre-specified
alert rule; alerts are then compared to the conventional clinical recognition
time R (Outcome.clinical_recognition_time).

Definitions (pre-specify in the SAP; defaults below):
* alert rule: probability >= threshold for ``consecutive`` successive predictions;
* true early detection: first alert in [R - max_lead_h, R + grace_h];
  lead time = R - first_alert (hours; positive = earlier than conventional care);
* false alert: an alert outside any true window (non-event episodes, or
  before R - max_lead_h in event episodes). Alerts within ``refractory_h`` of a
  previous alert are merged into one alert episode;
* only predictions before R + grace_h are evaluated in event episodes
  (after recognition the patient is already treated).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from irtcri.evaluation.metrics import cluster_bootstrap


@dataclass(frozen=True)
class AlertRule:
    threshold: float
    consecutive: int = 1
    max_lead_h: float = 72.0
    grace_h: float = 0.0
    refractory_h: float = 24.0
    lead_time_targets_h: tuple[float, ...] = (0.0, 6.0, 12.0, 24.0, 48.0)


def _alerts(times: pd.Series, probs: pd.Series, rule: AlertRule) -> list[pd.Timestamp]:
    above = (probs.to_numpy() >= rule.threshold).astype(int)
    run, out, last = 0, [], None
    for t, a in zip(times, above, strict=True):
        run = run + 1 if a else 0
        if run >= rule.consecutive:
            if last is None or (t - last).total_seconds() / 3600 >= rule.refractory_h:
                out.append(t)
                last = t
    return out


def episode_alerts(pred: pd.DataFrame, episodes: pd.DataFrame, rule: AlertRule) -> pd.DataFrame:
    """``pred``: episode_id, prediction_time, prob. ``episodes``: episode_id, patient_id, site_id, catheter_type,
    start_time, stop_time, event (0/1), recognition_time."""
    rows = []
    grace = pd.Timedelta(hours=rule.grace_h)
    lead_max = pd.Timedelta(hours=rule.max_lead_h)
    for e in episodes.itertuples():
        p = pred[pred["episode_id"] == e.episode_id].sort_values("prediction_time")
        ev = bool(e.event) and pd.notna(e.recognition_time)
        horizon_end = (e.recognition_time + grace) if ev else e.stop_time
        p = p[p["prediction_time"] <= horizon_end]
        alerts = _alerts(p["prediction_time"], p["prob"], rule)
        true_win_start = e.recognition_time - lead_max if ev else None
        true_alerts = [a for a in alerts if ev and true_win_start <= a <= horizon_end]
        false_alerts = [a for a in alerts if a not in true_alerts]
        first = true_alerts[0] if true_alerts else None
        observed_h = max((horizon_end - e.start_time).total_seconds() / 3600, 0)
        at_risk_h = observed_h - (rule.max_lead_h + rule.grace_h if ev else 0)
        rows.append({
            "episode_id": e.episode_id, "patient_id": e.patient_id, "site_id": getattr(e, "site_id", None),
            "catheter_type": getattr(e, "catheter_type", None), "event": int(ev), "n_predictions": int(len(p)),
            "detected": int(first is not None) if ev else np.nan,
            "lead_time_h": (e.recognition_time - first).total_seconds() / 3600 if first is not None else np.nan,
            "n_false_alerts": len(false_alerts), "any_alert": int(bool(alerts)),
            "observed_days": observed_h / 24, "false_alert_at_risk_days": max(at_risk_h, 0) / 24,
            "first_prediction_lead_h": (e.recognition_time - p["prediction_time"].min()).total_seconds() / 3600
            if ev and len(p) else np.nan,
        })
    return pd.DataFrame(rows)


def summarize(ea: pd.DataFrame, rule: AlertRule) -> dict[str, float]:
    ev = ea[ea["event"] == 1]
    ne = ea[ea["event"] == 0]
    det = ev[ev["detected"] == 1]
    out = {
        "n_episodes": int(len(ea)), "n_events": int(len(ev)),
        "event_sensitivity": float(ev["detected"].mean()) if len(ev) else np.nan,
        "episode_specificity": float(1 - ne["any_alert"].mean()) if len(ne) else np.nan,
        "median_lead_time_h": float(det["lead_time_h"].median()) if len(det) else np.nan,
        "lead_time_q1_h": float(det["lead_time_h"].quantile(.25)) if len(det) else np.nan,
        "lead_time_q3_h": float(det["lead_time_h"].quantile(.75)) if len(det) else np.nan,
        "false_alerts_per_catheter_day": float(ea["n_false_alerts"].sum() / ea["false_alert_at_risk_days"].sum())
        if ea["false_alert_at_risk_days"].sum() > 0 else np.nan,
        "false_alerts_per_patient_day": float(ea.groupby("patient_id")["n_false_alerts"].sum().sum()
                                              / ea.groupby("patient_id")["observed_days"].sum().sum())
        if ea["observed_days"].sum() > 0 else np.nan,
        "alert_ppv": float(len(det) / (len(det) + ea["n_false_alerts"].sum()))
        if (len(det) + ea["n_false_alerts"].sum()) else np.nan,
    }
    for L in rule.lead_time_targets_h:
        # sensitivity at lead time >= L: event detected at least L hours before conventional recognition
        out[f"sensitivity_lead_ge_{int(L)}h"] = float((ev["lead_time_h"] >= L).mean()) if len(ev) else np.nan
    out["proportion_detected_ge_6h_among_detected"] = float((det["lead_time_h"] >= 6).mean()) if len(det) else np.nan
    # events whose first evaluable prediction was too late to allow a >= 6 h lead (design limitation, report!)
    out["events_without_6h_opportunity"] = int((ev["first_prediction_lead_h"] < 6).sum()) if len(ev) else 0
    return out


def evaluate_early_warning(pred: pd.DataFrame, episodes: pd.DataFrame, rule: AlertRule, n_boot: int = 1000,
                           seed: int = 0) -> dict:
    ea = episode_alerts(pred, episodes, rule)
    ci = cluster_bootstrap(ea, lambda d: summarize(d, rule), "patient_id", n_boot, seed) if len(ea) else {}
    return {"rule": asdict(rule), "summary": ci, "per_episode": ea}


def lead_time_distribution(ea: pd.DataFrame) -> pd.Series:
    return ea.loc[(ea["event"] == 1) & (ea["detected"] == 1), "lead_time_h"].dropna()
