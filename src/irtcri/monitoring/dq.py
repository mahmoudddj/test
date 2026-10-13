"""Data-quality and study-monitoring metrics for the multicentre study.

All metrics are computed from a hub snapshot (``pipeline.snapshot.load_tables``)
and returned as JSON-serialisable structures for the dashboard and reports.
Drill-down lists contain pseudonymous Study IDs only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from irtcri.schemas.dictionary import load_dictionary


def _records(df: pd.DataFrame, limit: int | None = 500) -> list[dict]:
    if df.empty:
        return []
    d = df.head(limit) if limit else df
    d = d.copy()
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return json_safe(d.to_dict("records"))


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if obj is pd.NaT or (obj is not None and not isinstance(obj, (str, bool, int)) and pd.isna(obj)):
        return None
    return obj


def episode_windows(t: dict[str, pd.DataFrame], now: pd.Timestamp) -> pd.DataFrame:
    ep = t["catheter_episode"].copy()
    if ep.empty:
        return ep.assign(window_end=pd.Series(dtype="datetime64[ns, UTC]"), duration_h=[], expected_images=[])
    end = ep["removal_time"].combine_first(ep["end_of_follow_up_time"])
    ep["window_end"] = end.fillna(now).clip(upper=now)
    ep["duration_h"] = (ep["window_end"] - ep["follow_up_start_time"]).dt.total_seconds() / 3600
    ep["duration_h"] = ep["duration_h"].clip(lower=0)
    ep["expected_images"] = np.floor(ep["duration_h"] / ep["imaging_interval_hours"]).astype(int) + 1
    ep["closed"] = end.notna() & (end <= now)
    return ep


def imaging_completeness(t: dict[str, pd.DataFrame], now: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-episode expected vs collected thermal timepoints and the list of missing slots.

    A slot k covers [start + k*interval, start + (k+1)*interval). A slot counts as
    collected if it holds >= 1 thermal image whose automated or overridden QC is not FAIL.
    """
    ep = episode_windows(t, now)
    th = t["thermal_image"].copy()
    if not th.empty:
        th["eff_qc"] = th["qc_override_status"].fillna(th["qc_status"])
        th = th[th["eff_qc"] != "FAIL"]
    rows, missing = [], []
    for e in ep.itertuples():
        imgs = th[th["episode_id"] == e.id] if not th.empty else th
        interval = pd.Timedelta(hours=e.imaging_interval_hours)
        slots = set()
        if len(imgs):
            k = ((imgs["acquisition_time"] - e.follow_up_start_time) / interval).apply(np.floor).astype(int)
            slots = set(k[(k >= 0) & (k < e.expected_images)])
        rows.append({"episode_id": e.id, "episode_code": e.episode_code, "patient_id": e.patient_id,
                     "site_id": e.site_id, "expected": int(e.expected_images), "collected": len(slots),
                     "images_total": int(len(imgs))})
        for k in range(int(e.expected_images)):
            if k not in slots:
                missing.append({"episode_code": e.episode_code, "patient_id": e.patient_id, "site_id": e.site_id,
                                "slot": k, "slot_start": e.follow_up_start_time + k * interval})
    return pd.DataFrame(rows), pd.DataFrame(missing)


def clinical_completeness(t: dict[str, pd.DataFrame], now: pd.Timestamp) -> pd.DataFrame:
    """Completeness of required repeated variables per site: observed / expected measurements."""
    d = load_dictionary()
    req = [v for v in d.variables if v.required and v.entity in ("observation", "laboratory")
           and v.expected_interval_hours]
    ep = episode_windows(t, now)
    if ep.empty:
        return pd.DataFrame(columns=["site_id", "variable", "expected", "observed", "completeness"])
    meas = pd.concat([t["clinical_observation"][["patient_id", "variable", "event_time", "value_num"]],
                      t["laboratory_result"][["patient_id", "variable", "event_time", "value_num"]]])
    meas = meas[meas["value_num"].notna()]
    out = []
    for site_id, eps in ep.groupby("site_id"):
        for v in req:
            expected = observed = 0
            for e in eps.itertuples():
                interval = pd.Timedelta(hours=v.expected_interval_hours)
                n_slots = int(np.floor(e.duration_h / v.expected_interval_hours)) + 1
                m = meas[(meas["patient_id"] == e.patient_id) & (meas["variable"] == v.name)
                         & (meas["event_time"] >= e.follow_up_start_time) & (meas["event_time"] < e.window_end)]
                slots = set(((m["event_time"] - e.follow_up_start_time) / interval).apply(np.floor).astype(int))
                expected += n_slots
                observed += len([s for s in slots if 0 <= s < n_slots])
            out.append({"site_id": site_id, "variable": v.name, "expected": expected, "observed": observed,
                        "completeness": observed / expected if expected else None})
    return pd.DataFrame(out)


def compute_dq(t: dict[str, pd.DataFrame], now: datetime | None = None) -> dict[str, Any]:
    now = pd.Timestamp(now or pd.Timestamp.now(tz="UTC"))
    site = t["site"].set_index("id")["site_code"].to_dict()
    pat = t["patient"]
    sid = pat.set_index("id")["study_id"].to_dict()
    ep = t["catheter_episode"]
    th = t["thermal_image"]
    res: dict[str, Any] = {"generated_time": now.isoformat(), "sites": [
        {"site_id": k, "site_code": v} for k, v in site.items()]}

    # recruitment ---------------------------------------------------------
    rec = pat.assign(site_code=pat["site_id"].map(site),
                     month=pd.to_datetime(pat["enrolment_date"]).dt.strftime("%Y-%m")) if len(pat) else pat
    res["recruitment"] = {
        "enrolled_total": int(len(pat)),
        "by_site": rec.groupby("site_code").size().to_dict() if len(pat) else {},
        "by_site_month": (rec.groupby(["site_code", "month"]).size().rename("n").reset_index().to_dict("records")
                          if len(pat) else []),
        "screened_by_site": t["screening_log"].assign(site_code=t["screening_log"]["site_id"].map(site))
        .groupby("site_code").size().to_dict() if len(t["screening_log"]) else {},
        "withdrawn": int(pat["withdrawn_time"].notna().sum()) if len(pat) else 0,
    }
    res["episodes"] = {
        "total": int(len(ep)),
        "by_site": ep.assign(site_code=ep["site_id"].map(site)).groupby("site_code").size().to_dict() if len(ep)
        else {},
        "by_catheter_type": ep.groupby("catheter_type").size().to_dict() if len(ep) else {},
        "patients_with_multiple_episodes": int((ep.groupby("patient_id").size() > 1).sum()) if len(ep) else 0,
    }

    # imaging ---------------------------------------------------------------
    comp, missing = imaging_completeness(t, now)
    per_pat = th.groupby("patient_id").size() if len(th) else pd.Series(dtype=int)
    res["imaging"] = {
        "thermal_images": int(len(th)), "rgb_images": int(len(t["rgb_image"])),
        "images_per_patient": {"median": float(per_pat.median()) if len(per_pat) else None,
                               "q1": float(per_pat.quantile(.25)) if len(per_pat) else None,
                               "q3": float(per_pat.quantile(.75)) if len(per_pat) else None},
        "expected_timepoints": int(comp["expected"].sum()) if len(comp) else 0,
        "collected_timepoints": int(comp["collected"].sum()) if len(comp) else 0,
        "by_site": (comp.assign(site_code=comp["site_id"].map(site)).groupby("site_code")[["expected", "collected"]]
                    .sum().assign(completeness=lambda x: x["collected"] / x["expected"]).reset_index()
                    .to_dict("records")) if len(comp) else [],
        "missing_timepoints": _records(missing.assign(study_id=missing["patient_id"].map(sid),
                                                      site_code=missing["site_id"].map(site))
                                       .drop(columns=["patient_id", "site_id"])) if len(missing) else [],
    }
    # QC ------------------------------------------------------------------------
    qc = {}
    for name in ("thermal_image", "rgb_image"):
        df = t[name]
        if df.empty:
            qc[name] = {"by_status": {}, "by_site": [], "top_reasons": {}, "failed": []}
            continue
        reasons = pd.Series([r["check"] + ":" + r["severity"] for rs in df["qc_reasons"].dropna() for r in rs])
        failed = df[df["qc_status"] == "FAIL"]
        qc[name] = {
            "by_status": df["qc_status"].value_counts().to_dict(),
            "by_site": df.assign(site_code=df["site_id"].map(site)).groupby(["site_code", "qc_status"]).size()
            .rename("n").reset_index().to_dict("records"),
            "top_reasons": reasons.value_counts().head(15).to_dict(),
            "overridden": int(df["qc_override_status"].notna().sum()),
            "failed": _records(failed.assign(study_id=failed["patient_id"].map(sid))[
                ["id", "study_id", "acquisition_time", "qc_reasons"]]),
        }
    res["qc"] = qc

    # clinical completeness -------------------------------------------------
    cc = clinical_completeness(t, now)
    res["clinical_completeness"] = cc.assign(site_code=cc["site_id"].map(site)).drop(columns=["site_id"]).to_dict(
        "records") if len(cc) else []

    # outcomes -----------------------------------------------------------------
    epw = episode_windows(t, now)
    oc = t["outcome"]
    has_oc = set(oc["episode_id"]) if len(oc) else set()
    closed_no_oc = epw[epw["closed"] & ~epw["id"].isin(has_oc)] if len(epw) else epw
    res["outcomes"] = {
        "episodes_closed": int(epw["closed"].sum()) if len(epw) else 0,
        "missing_outcome_label": _records(closed_no_oc.assign(study_id=closed_no_oc["patient_id"].map(sid))[
            ["episode_code", "study_id", "window_end"]]) if len(closed_no_oc) else [],
        "provisional": int((oc["status"] == "provisional").sum()) if len(oc) else 0,
        "events": int(oc["is_event"].sum()) if len(oc) else 0,
        "event_prevalence_by_episode": float(oc["is_event"].mean()) if len(oc) else None,
        "by_outcome_type": oc["outcome_type"].value_counts().to_dict() if len(oc) else {},
        "events_by_site": oc.merge(ep[["id", "site_id"]], left_on="episode_id", right_on="id", suffixes=("", "_e"))
        .assign(site_code=lambda x: x["site_id"].map(site)).groupby("site_code")["is_event"].agg(["sum", "count"])
        .reset_index().to_dict("records") if len(oc) else [],
    }

    # queries / deviations ------------------------------------------------------
    q = t["data_query"]
    openq = q[q["status"] != "closed"] if len(q) else q
    res["queries"] = {
        "open": int(len(openq)),
        "by_site": openq.assign(site_code=openq["site_id"].map(site)).groupby("site_code").size().to_dict()
        if len(openq) else {},
        "median_age_days": float(((now - openq["raised_time"]).dt.total_seconds() / 86400).median())
        if len(openq) else None,
        "auto_generated_open": int(openq["auto_generated"].sum()) if len(openq) else 0,
    }
    dv = t["protocol_deviation"]
    res["deviations"] = {
        "total": int(len(dv)),
        "open": int((dv["status"] != "closed").sum()) if len(dv) else 0,
        "by_site_severity": dv.assign(site_code=dv["site_id"].map(site)).groupby(["site_code", "severity"]).size()
        .rename("n").reset_index().to_dict("records") if len(dv) else [],
        "by_category": dv["category"].value_counts().to_dict() if len(dv) else {},
    }

    # annotation progress -----------------------------------------------------------
    an = t["annotation"]
    sub = an[an["status"] == "submitted"] if len(an) else an
    eligible = th[(th["qc_override_status"].fillna(th["qc_status"]) != "FAIL")] if len(th) else th
    r1 = set(sub[sub["reader_role"] == "reader_1"]["thermal_image_id"]) if len(sub) else set()
    r2 = set(sub[sub["reader_role"] == "reader_2"]["thermal_image_id"]) if len(sub) else set()
    adj = t["adjudication"]
    res["annotation"] = {
        "eligible_images": int(len(eligible)), "reader_1_done": len(r1), "reader_2_done": len(r2),
        "both_done": len(r1 & r2),
        "adjudicated_images": int(adj[(adj["level"] == "image") & adj["is_current"]]["thermal_image_id"].nunique())
        if len(adj) else 0,
    }

    # latency ---------------------------------------------------------------------------
    lat = []
    for name in ("clinical_observation", "laboratory_result", "thermal_image", "clinical_assessment"):
        df = t[name]
        if df.empty:
            continue
        tcol = "acquisition_time" if name == "thermal_image" else "event_time"
        h = (df["recorded_time"] - df[tcol]).dt.total_seconds() / 3600
        g = df.assign(h=h, site_code=(df["site_id"] if "site_id" in df else df["patient_id"].map(
            pat.set_index("id")["site_id"])).map(site)).groupby("site_code")["h"]
        for sc, s in g:
            lat.append({"table": name, "site_code": sc, "median_hours": float(s.median()),
                        "p90_hours": float(s.quantile(.9))})
    res["data_latency"] = lat

    # duplicates and impossible / flagged values ------------------------------------
    obs = t["clinical_observation"]
    dup_obs = obs[obs.duplicated(["patient_id", "variable", "event_time"], keep=False)] if len(obs) else obs
    res["duplicates"] = {
        "duplicate_images": int(th["duplicate_of_id"].notna().sum()) if len(th) else 0,
        "duplicate_observations": int(len(dup_obs)),
        "duplicate_observation_examples": _records(dup_obs.assign(study_id=dup_obs["patient_id"].map(sid))[
            ["study_id", "variable", "event_time", "value_num"]], 50) if len(dup_obs) else [],
    }
    flagged = []
    for name in ("clinical_observation", "laboratory_result", "patient", "clinical_assessment"):
        df = t[name]
        if df.empty or "dq_flags" not in df:
            continue
        f = df[df["dq_flags"].notna() & df["dq_flags"].astype(bool)]
        for r in f.itertuples():
            flagged.append({"table": name, "id": r.id,
                            "study_id": sid.get(getattr(r, "patient_id", None), getattr(r, "study_id", None)),
                            "flags": r.dq_flags})
    ib = t["import_batch"]
    res["value_checks"] = {
        "flagged_values": len(flagged), "flagged_examples": flagged[:200],
        "import_rejected_rows": int(ib["rows_rejected"].sum()) if len(ib) else 0,
        "import_batches": int(len(ib)),
    }

    # cross-site comparison -----------------------------------------------------------
    cs = []
    if len(th):
        g = th.assign(site_code=th["site_id"].map(site),
                      fail=(th["qc_status"] == "FAIL").astype(float)).groupby("site_code")
        for sc, d in g:
            cs.append({"site_code": sc, "n_images": int(len(d)), "qc_fail_rate": float(d["fail"].mean()),
                       "median_t_mean": float(d["t_mean"].median()) if d["t_mean"].notna().any() else None,
                       "median_ambient": float(d["ambient_temperature_c"].median())
                       if d["ambient_temperature_c"].notna().any() else None,
                       "median_distance_cm": float(d["distance_cm"].median()) if d["distance_cm"].notna().any()
                       else None,
                       "share_missing_ambient": float(d["ambient_temperature_c"].isna().mean()),
                       "camera_models": sorted(d["camera_model"].dropna().unique().tolist())})
    res["cross_site"] = cs
    return json_safe(res)


def patient_drilldown(t: dict[str, pd.DataFrame], study_id: str, now: datetime | None = None) -> dict:
    now = pd.Timestamp(now or pd.Timestamp.now(tz="UTC"))
    pat = t["patient"][t["patient"]["study_id"] == study_id]
    if pat.empty:
        return {}
    pid = pat.iloc[0]["id"]
    sub = {k: (v[v["patient_id"] == pid] if "patient_id" in v.columns else v) for k, v in t.items()}
    sub["catheter_episode"] = t["catheter_episode"][t["catheter_episode"]["patient_id"] == pid]
    comp, missing = imaging_completeness(sub, now)
    q = t["data_query"]
    ids = set()
    for name in ("clinical_observation", "laboratory_result", "thermal_image", "clinical_assessment", "outcome"):
        ids |= set(sub[name]["id"])
    ids |= {pid}
    return json_safe({
        "study_id": study_id,
        "imaging": comp.to_dict("records"),
        "missing_timepoints": _records(missing.drop(columns=["patient_id"])) if len(missing) else [],
        "open_queries": _records(q[(q["entity_id"].isin(ids)) & (q["status"] != "closed")]) if len(q) else [],
        "qc_failed_images": _records(sub["thermal_image"][sub["thermal_image"]["qc_status"] == "FAIL"][
            ["id", "acquisition_time", "qc_reasons"]]),
    })
