"""FUTURE-DATA and PATIENT-LEVEL leakage tests (critical for valid ML evaluation)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from irtcri.pipeline.labels import LabelConfig, label_samples
from irtcri.pipeline.splits import (
    PatientLeakageError,
    assert_no_patient_leakage,
    patient_kfold,
    patient_level_split,
)
from irtcri.pipeline.temporal import (
    FutureLeakageError,
    add_available_time,
    assert_no_future_leakage,
    nearest_prior,
    window_features,
)

T = pd.Timestamp("2026-03-01T00:00:00Z")


def h(x):
    return T + pd.Timedelta(hours=x)


def _meas(rows):
    m = pd.DataFrame(rows, columns=["patient_id", "variable", "value_num", "event_time", "source_time"])
    return add_available_time(m)


def test_lab_reported_after_prediction_time_is_not_used():
    # WBC drawn at 08:00 (before prediction at 09:00) but reported at 12:00 -> must NOT be visible at 09:00
    meas = _meas([("p1", "wbc", 20.0, h(8), h(12)), ("p1", "wbc", 7.0, h(-20), h(-18))])
    s = pd.DataFrame({"patient_id": ["p1", "p1"], "prediction_time": [h(9), h(13)]})
    f = window_features(s, meas, ["wbc"], lookback_h=48, aggregations=["last", "count"])
    assert f.loc[0, "wbc__last"] == 7.0 and f.loc[0, "wbc__count"] == 1
    assert f.loc[1, "wbc__last"] == 20.0 and f.loc[1, "wbc__count"] == 2
    assert (f["_max_available_time"] <= s["prediction_time"]).all()
    assert nearest_prior(s, meas, "wbc").tolist() == [7.0, 20.0]


def test_missing_source_time_uses_conservative_lag():
    # lab with no report time: available at collection + dictionary lag (wbc: 120 min)
    meas = _meas([("p1", "wbc", 11.0, h(8), pd.NaT)])
    assert meas["available_time"].iloc[0] == h(10)
    s = pd.DataFrame({"patient_id": ["p1"], "prediction_time": [h(9)]})
    assert np.isnan(window_features(s, meas, ["wbc"], 24, ["last"]).loc[0, "wbc__last"])


def test_future_measurements_never_enter_features():
    rng = np.random.default_rng(0)
    rows = []
    for p in ("a", "b"):
        for _ in range(200):
            et = h(float(rng.uniform(-48, 96)))
            rows.append((p, "temperature_c", float(rng.normal(37, 1)), et, pd.NaT))
    meas = _meas(rows)
    s = pd.DataFrame({"patient_id": ["a", "b"] * 10, "prediction_time": [h(x) for x in range(0, 60, 3)]})
    f = window_features(s, meas, ["temperature_c"], 24, ["last", "max", "count"])
    s2 = pd.concat([s, f], axis=1)
    assert_no_future_leakage(s2)
    # brute-force reference
    for i, r in s.iterrows():
        m = meas[(meas.patient_id == r.patient_id) & (meas.available_time <= r.prediction_time)
                 & (meas.event_time >= r.prediction_time - pd.Timedelta(hours=24))]
        assert f.loc[i, "temperature_c__count"] == len(m)
        if len(m):
            assert f.loc[i, "temperature_c__max"] == pytest.approx(m.value_num.max())


def test_leakage_assertion_fires():
    s = pd.DataFrame({"prediction_time": [h(0)], "_max_available_time": [h(1)]})
    with pytest.raises(FutureLeakageError):
        assert_no_future_leakage(s)


def test_post_recognition_samples_excluded_and_horizon_labels():
    samples = pd.DataFrame({"episode_id": ["e1"] * 5 + ["e2"], "prediction_time": [h(0), h(30), h(50), h(72), h(80),
                                                                                 h(10)]})
    oc = pd.DataFrame({"episode_id": ["e1", "e2"], "is_event": [True, False],
                       "clinical_recognition_time": [h(72), pd.NaT], "onset_time_reference": [h(40), pd.NaT],
                       "status": ["adjudicated"] * 2, "outcome_type": ["exit_site_infection", "no_infection"]})
    out = label_samples(samples, oc, LabelConfig(mode="early_warning", horizon_hours=48))
    assert out["label_status"].tolist()[:5] == ["included"] * 4 + ["excluded_post_recognition"]
    assert out["label"].tolist()[:4] == [0, 1, 1, 1]   # h0 is > 48 h before recognition
    assert out["label"].iloc[5] == 0
    det = label_samples(samples, oc, LabelConfig(mode="detection"))
    assert det["label"].tolist()[:4] == [0, 0, 1, 1]


def test_patient_level_split_has_no_overlap_and_is_deterministic():
    rng = np.random.default_rng(1)
    pats = pd.DataFrame({"patient_id": [f"p{i}" for i in range(100)],
                         "has_event": rng.random(100) < 0.3, "site_id": rng.choice(["s1", "s2", "s3"], 100)})
    a = patient_level_split(pats, seed=5)
    b = patient_level_split(pats, seed=5)
    assert (a == b).all()
    images = pats.loc[pats.index.repeat(20)].reset_index(drop=True)
    images["split"] = images["patient_id"].map(a)
    assert_no_patient_leakage(images)
    assert set(a.unique()) == {"train", "val", "test"}
    # every split contains events (stratification)
    ev = pats.set_index("patient_id")["has_event"]
    for sp in ("train", "val", "test"):
        assert ev[a[a == sp].index].sum() > 0
    images.loc[0, "split"] = "test" if images.loc[0, "split"] != "test" else "train"
    with pytest.raises(PatientLeakageError):
        assert_no_patient_leakage(images)


def test_site_holdout_and_kfold():
    pats = pd.DataFrame({"patient_id": [f"p{i}" for i in range(30)], "has_event": [i % 3 == 0 for i in range(30)],
                         "site_id": ["s1"] * 10 + ["s2"] * 10 + ["s3"] * 10})
    a = patient_level_split(pats, 0, holdout_sites=["s3"])
    assert (a[[f"p{i}" for i in range(20, 30)]] == "external_test").all()
    assert "external_test" not in a[[f"p{i}" for i in range(20)]].values
    f = patient_kfold(pats, 5, 0)
    assert f.nunique() == 5 and f.index.is_unique
