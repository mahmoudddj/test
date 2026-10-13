"""Known-answer tests for the evaluation and biostatistics library."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from irtcri.annotation.agreement import cohen_kappa
from irtcri.evaluation.calibration import calibration_intercept_slope, expected_calibration_error
from irtcri.evaluation.clustered import gee_logistic, logistic
from irtcri.evaluation.early_warning import AlertRule, episode_alerts, summarize
from irtcri.evaluation.metrics import binary_metrics, cluster_bootstrap, metrics_with_ci, wilson_ci
from irtcri.evaluation.survival import cox_ph, kaplan_meier, logrank_test
from irtcri.ml.thresholds import select_threshold

T = pd.Timestamp("2026-01-01T00:00Z")


def test_confusion_metrics():
    y = [1, 1, 1, 0, 0, 0, 0, 0]
    p = [0.9, 0.8, 0.2, 0.7, 0.1, 0.1, 0.3, 0.2]
    m = binary_metrics(y, p, 0.5)
    assert (m["tp"], m["fn"], m["fp"], m["tn"]) == (2, 1, 1, 4)
    assert m["sensitivity"] == pytest.approx(2 / 3) and m["specificity"] == pytest.approx(0.8)
    assert m["ppv"] == pytest.approx(2 / 3) and m["npv"] == pytest.approx(0.8)
    assert m["f1"] == pytest.approx(2 / 3)
    assert m["auroc"] == pytest.approx(12.5 / 15)   # one tied pair counts 0.5


def test_wilson():
    lo, hi = wilson_ci(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)


def test_cluster_bootstrap_resamples_patients():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"patient_id": np.repeat(np.arange(40), 10), "label": np.repeat(rng.random(40) < .4, 10),
                       "prob": rng.random(400)})
    res = metrics_with_ci(df, 0.5, n_boot=200)
    assert res["counts"]["n_clusters"] == 40
    a = res["sensitivity"]
    assert a["ci_low"] <= a["estimate"] <= a["ci_high"]
    # cluster bootstrap must give wider CI than naive i.i.d. for perfectly clustered outcomes
    naive = cluster_bootstrap(df.assign(row=np.arange(400)), lambda d: {"m": d["label"].mean()}, "row", 300)
    clus = cluster_bootstrap(df, lambda d: {"m": d["label"].mean()}, "patient_id", 300)
    assert (clus["m"]["ci_high"] - clus["m"]["ci_low"]) > 2 * (naive["m"]["ci_high"] - naive["m"]["ci_low"])


def test_calibration_perfect_model():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.05, 0.95, 20000)
    y = (rng.random(20000) < p).astype(int)
    c = calibration_intercept_slope(y, p)
    assert c["slope"] == pytest.approx(1.0, abs=0.08) and c["intercept_citl"] == pytest.approx(0, abs=0.05)
    assert expected_calibration_error(y, p) < 0.02


def test_threshold_selection_target_sensitivity():
    y = np.array([1] * 10 + [0] * 90)
    p = np.concatenate([np.linspace(0.5, 0.95, 10), np.linspace(0.0, 0.6, 90)])
    thr, info = select_threshold(y, p, "target_sensitivity", 0.85)
    assert binary_metrics(y, p, thr)["sensitivity"] >= 0.85 and info["selected_on"] == "validation"


def test_early_warning_lead_time_and_false_alerts():
    eps = pd.DataFrame({
        "episode_id": ["e1", "e2"], "patient_id": ["p1", "p2"], "site_id": ["s", "s"], "catheter_type": ["CVC"] * 2,
        "start_time": [T, T], "stop_time": [T + pd.Timedelta(hours=100), T + pd.Timedelta(hours=96)],
        "event": [1, 0], "recognition_time": [T + pd.Timedelta(hours=100), pd.NaT]})
    times = [T + pd.Timedelta(hours=h) for h in range(0, 97, 12)]
    pred = pd.DataFrame({"episode_id": ["e1"] * len(times) + ["e2"] * len(times),
                         "prediction_time": times * 2,
                         "prob": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.9, 0.9, 0.9] + [0.1, 0.8, 0.1, 0.1, 0.1, 0.1, 0.1,
                                                                                    0.1, 0.1]})
    rule = AlertRule(threshold=0.5, consecutive=1, max_lead_h=72, refractory_h=24)
    ea = episode_alerts(pred, eps, rule).set_index("episode_id")
    assert ea.loc["e1", "detected"] == 1 and ea.loc["e1", "lead_time_h"] == pytest.approx(28)
    assert ea.loc["e2", "n_false_alerts"] == 1 and ea.loc["e1", "n_false_alerts"] == 0
    s = summarize(ea.reset_index(), rule)
    assert s["event_sensitivity"] == 1 and s["sensitivity_lead_ge_6h"] == 1 and s["sensitivity_lead_ge_48h"] == 0
    assert s["episode_specificity"] == 0
    # consecutive=2 suppresses the isolated false alert
    ea2 = episode_alerts(pred, eps, AlertRule(threshold=0.5, consecutive=2)).set_index("episode_id")
    assert ea2.loc["e2", "n_false_alerts"] == 0 and ea2.loc["e1", "lead_time_h"] == pytest.approx(16)


def test_kaplan_meier_known_values():
    km = kaplan_meier([1, 2, 2, 3, 4, 5], [1, 1, 0, 1, 0, 1])
    s = km.set_index("time")["survival"]
    assert s[1] == pytest.approx(5 / 6) and s[2] == pytest.approx(5 / 6 * 4 / 5)
    assert s[3] == pytest.approx(5 / 6 * 4 / 5 * 2 / 3) and s[5] == pytest.approx(0)


def test_logrank_and_cox_detect_effect():
    rng = np.random.default_rng(3)
    n = 300
    g = rng.integers(0, 2, n)
    t = rng.exponential(np.where(g == 1, 50, 100))
    c = rng.uniform(0, 150, n)
    d = pd.DataFrame({"time": np.minimum(t, c), "event": (t <= c).astype(int), "g": g.astype(float),
                      "patient_id": np.arange(n)})
    assert logrank_test(d["time"], d["event"], d["g"])["p_value"] < 0.01
    hr = cox_ph(d, "time", "event", ["g"]).iloc[0]
    assert 1.4 < hr["hr"] < 3.0 and hr["ci_low"] > 1


def test_gee_and_logistic_guard():
    rng = np.random.default_rng(4)
    pid = np.repeat(np.arange(60), 8)
    u = np.repeat(rng.normal(0, 1, 60), 8)
    x = rng.normal(0, 1, 480)
    y = (rng.random(480) < 1 / (1 + np.exp(-(-0.5 + 1.0 * x + u)))).astype(int)
    df = pd.DataFrame({"patient_id": pid, "x": x, "y": y})
    g = gee_logistic(df, "y ~ x").set_index("term")
    assert g.loc["x", "odds_ratio"] > 1.5
    with pytest.raises(ValueError):
        logistic(df, "y ~ x", unit_col="patient_id")


def test_kappa():
    assert cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == pytest.approx(1)
    assert cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == pytest.approx(0)
