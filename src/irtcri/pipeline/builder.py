"""Reproducible dataset builder: raw -> validated -> longitudinal -> ML -> locked.

Usage: ``irtcri build-dataset configs/datasets/primary_early_warning.yaml``
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.config import get_settings
from irtcri.core.provenance import code_version, sha256_file, sha256_json
from irtcri.core.timeutils import utcnow
from irtcri.db.models import DatasetVersion, RGBImage, ThermalImage
from irtcri.imaging.derive import RGBPreprocessConfig, ThermalPreprocessConfig, derive_rgb, derive_thermal
from irtcri.pipeline.cohort import CohortFlow, screening_counts
from irtcri.pipeline.labels import LabelConfig, label_samples, time_to_event
from irtcri.pipeline.snapshot import load_tables
from irtcri.pipeline.splits import assert_no_patient_leakage, patient_level_split, split_fingerprint
from irtcri.pipeline.temporal import assert_no_future_leakage, missing_indicators, unify_measurements, window_features
from irtcri.schemas.dictionary import load_dictionary


class LockedDatasetError(RuntimeError):
    pass


def load_spec(spec: str | Path | dict) -> dict:
    if isinstance(spec, dict):
        return spec
    with open(spec) as f:
        return yaml.safe_load(f)


def _write(df: pd.DataFrame, path: Path, csv: bool = False) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path.with_suffix(".parquet"), index=False)
    files = {path.with_suffix(".parquet").name: sha256_file(path.with_suffix(".parquet"))}
    if csv:
        df.to_csv(path.with_suffix(".csv"), index=False)
        files[path.with_suffix(".csv").name] = sha256_file(path.with_suffix(".csv"))
    return files


def _site_ids(tables: dict[str, pd.DataFrame], spec_sites) -> list[str]:
    s = tables["site"]
    if spec_sites in (None, "all"):
        return s["id"].tolist()
    return s[s["site_code"].isin(spec_sites)]["id"].tolist()


def build_dataset(session: Session, spec: str | Path | dict, out_root: str | Path | None = None,
                  now: pd.Timestamp | None = None, derive_images: bool = True) -> dict[str, Any]:
    spec = load_spec(spec)
    name, version = spec["name"], spec["version"]
    if session.scalar(select(DatasetVersion).where(DatasetVersion.name == name, DatasetVersion.version == version,
                                                   DatasetVersion.locked.is_(True))):
        raise LockedDatasetError(f"{name} v{version} is locked; bump the version to rebuild")
    now = pd.Timestamp(now or utcnow())
    root = Path(out_root or get_settings().data_dir) / "datasets" / name / version
    if root.exists():
        shutil.rmtree(root)
    files: dict[str, dict] = {}

    # ---------------------------------------------------------------- 1 raw layer
    raw = load_tables(session, include_voided=True)
    for t, df in raw.items():
        files[f"raw/{t}"] = _write(df, root / "raw" / t)

    # ---------------------------------------------------------- 2 validated layer
    t = load_tables(session, include_voided=False)
    flow = CohortFlow()
    screening_counts(t["screening_log"], t["patient"], flow)
    site_ids = _site_ids(t, spec["cohort"].get("sites", "all"))
    pat = t["patient"]
    excl = pat[~pat["site_id"].isin(site_ids)]
    if len(excl):
        flow.exclude("site not in dataset scope", patients=len(excl))
    pat = pat[pat["site_id"].isin(site_ids)]
    if spec["cohort"].get("exclude_withdrawn_without_data_consent", True):
        w = pat["withdrawn_time"].notna() & ~pat["data_use_after_withdrawal"].fillna(False).astype(bool)
        if w.any():
            flow.exclude("withdrew consent for data use", patients=int(w.sum()))
        pat = pat[~w]
    meas = unify_measurements(t["clinical_observation"], t["laboratory_result"])
    meas = meas[meas["patient_id"].isin(pat["id"])]
    files["validated/measurements"] = _write(meas, root / "validated" / "measurements")
    files["validated/patients"] = _write(pat, root / "validated" / "patients")

    # ------------------------------------------------------- 3 longitudinal layer
    ep = t["catheter_episode"]
    ep = ep[ep["patient_id"].isin(pat["id"])]
    n_ep0 = len(ep)
    no_ep = pat[~pat["id"].isin(ep["patient_id"])]
    if len(no_ep):
        flow.exclude("no catheter episode registered", patients=len(no_ep))
    ep = ep[ep["catheter_type"].isin(spec["cohort"]["catheter_types"])]
    if len(ep) < n_ep0:
        flow.exclude("catheter type out of scope", episodes=n_ep0 - len(ep))
    oc = t["outcome"]
    tte = time_to_event(ep, oc, now)
    short = tte["duration_h"] < spec["cohort"].get("min_follow_up_hours", 0)
    short &= tte["event"] == 0
    if short.any():
        flow.exclude(f"follow-up < {spec['cohort'].get('min_follow_up_hours')} h without event",
                     episodes=int(short.sum()))
    tte = tte[~short]
    if spec["cohort"].get("require_outcome", True):
        no_oc = ~tte["outcome_known"]
        if no_oc.any():
            flow.exclude("no outcome label", episodes=int(no_oc.sum()))
        tte = tte[~no_oc]
    ep = ep[ep["id"].isin(tte["episode_id"])]
    files["longitudinal/time_to_event"] = _write(tte, root / "longitudinal" / "time_to_event", csv=True)
    files["longitudinal/episodes"] = _write(ep, root / "longitudinal" / "episodes")
    abx = t["medication_exposure"]
    abx = abx[abx["is_antibiotic"].astype(bool) & abx["patient_id"].isin(pat["id"])]
    files["longitudinal/antibiotics"] = _write(abx, root / "longitudinal" / "antibiotics")
    files["longitudinal/assessments"] = _write(
        t["clinical_assessment"][t["clinical_assessment"]["episode_id"].isin(ep["id"])],
        root / "longitudinal" / "assessments")
    flow.add("included in longitudinal cohort", patients=int(ep["patient_id"].nunique()), episodes=int(len(ep)))

    # ------------------------------------------------------------- 4 ML layer
    th = t["thermal_image"]
    th = th[th["episode_id"].isin(ep["id"])].copy()
    flow.add("thermal images of included episodes", images=int(len(th)))
    th["eff_qc"] = th["qc_override_status"].fillna(th["qc_status"])
    ic = spec["images"]
    image_filters = [
        ("image QC status not accepted", lambda d: ~d["eff_qc"].isin(ic["qc_status"])),
        ("image view not used", lambda d: ~d["view"].isin(ic["views"])),
        ("image not radiometric", lambda d: ~d["is_radiometric"].astype(bool) if ic.get("require_radiometric")
         else pd.Series(False, index=d.index)),
    ]
    for desc, rule in image_filters:
        mask = rule(th)
        if mask.any():
            flow.exclude(desc, images=int(mask.sum()))
        th = th[~mask]
    pairs = t["image_pair"][["thermal_image_id", "rgb_image_id"]]
    samples = th.rename(columns={"id": "thermal_image_id", "acquisition_time": "prediction_time"})[
        ["thermal_image_id", "patient_id", "episode_id", "site_id", "prediction_time", "view", "t_max", "t_mean",
         "ambient_temperature_c", "qc_metrics", "eff_qc", "acquisition_id", "xnat_uri", "raw_sha256"]
    ].merge(pairs, on="thermal_image_id", how="left")
    lc = LabelConfig(**spec["labels"])
    samples = label_samples(samples, oc, lc)
    for status, n in samples["label_status"].value_counts().items():
        if status != "included":
            flow.exclude(f"sample {status.replace('excluded_', '').replace('_', ' ')}", images=int(n))
    samples = samples[samples["label_status"] == "included"].reset_index(drop=True)
    samples["label"] = samples["label"].astype(int)

    # features
    fs = spec["features"]
    feat = window_features(samples, meas, fs["variables"], fs["lookback_hours"], fs["aggregations"])
    clin_cols = [c for c in feat.columns if not c.startswith("_")]
    feat = feat.rename(columns={c: "clin__" + c for c in clin_cols})
    clin_cols = ["clin__" + c for c in clin_cols]
    samples = pd.concat([samples, feat], axis=1)
    if fs.get("missing_indicators"):
        last_cols = [c for c in clin_cols if c.endswith("__last")]
        samples = pd.concat([samples, missing_indicators(samples, last_cols)], axis=1)
    base = pat.set_index("id")
    for b in fs.get("baseline", []):
        v = samples["patient_id"].map(base[b])
        if b == "sex":
            samples["base__sex_female"] = (v == "female").astype(float).where(v.isin(["female", "male"]))
        else:
            samples[f"base__{b}"] = v.astype(float)
    epi = ep.set_index("id")
    if "catheter_type" in fs.get("episode", []):
        ct = samples["episode_id"].map(epi["catheter_type"])
        for c in sorted(spec["cohort"]["catheter_types"]):
            samples[f"ep__catheter_{c}"] = (ct == c).astype(float)
    if "tunnelled" in fs.get("episode", []):
        samples["ep__tunnelled"] = samples["episode_id"].map(epi["tunnelled"]).astype(float)
    if "hours_since_insertion" in fs.get("episode", []):
        samples["ep__hours_since_insertion"] = (
            samples["prediction_time"] - samples["episode_id"].map(epi["insertion_time"])).dt.total_seconds() / 3600
    if fs.get("thermal_summary"):
        qm = samples["qc_metrics"].apply(lambda d: d or {})
        samples["img__t_max"] = samples["t_max"]
        samples["img__t_mean"] = samples["t_mean"]
        samples["img__t_std"] = qm.apply(lambda d: d.get("t_std"))
        samples["img__t_p99"] = qm.apply(lambda d: d.get("t_p99"))
        samples["img__t_max_minus_ambient"] = samples["t_max"] - samples["ambient_temperature_c"]
    if fs.get("thermal_longitudinal"):
        samples = pd.concat([samples, longitudinal_thermal_features(
            samples, th, epi, fs.get("thermal_baseline_hours", 24), fs["lookback_hours"])], axis=1)
    if fs.get("antibiotics_in_lookback"):
        lb = pd.Timedelta(hours=fs["lookback_hours"])
        s_ab = samples[["patient_id", "prediction_time"]].reset_index().merge(
            abx[["patient_id", "start_time", "end_time", "source_time"]], on="patient_id")
        known = s_ab["source_time"].fillna(s_ab["start_time"])
        active = (s_ab["start_time"] <= s_ab["prediction_time"]) & (known <= s_ab["prediction_time"]) & (
            s_ab["end_time"].isna() | (s_ab["end_time"] >= s_ab["prediction_time"] - lb))
        on = set(s_ab[active]["index"])
        samples["clin__antibiotic_active"] = samples.index.isin(on).astype(float)
    samples = samples.drop(columns=["qc_metrics", "t_max", "t_mean"])
    samples = samples.drop(columns=[c for c in samples.columns if c.startswith("_img_history")])
    assert_no_future_leakage(samples, avail_cols=("_max_available_time", "_img_history_max_time"))

    # images
    if derive_images:
        tcfg = ThermalPreprocessConfig(size=ic["derive_thermal"].get("size", 256),
                                       normalization=ic["derive_thermal"].get("normalization", "fixed_window"),
                                       window_c=tuple(ic["derive_thermal"].get("window_c", (20.0, 40.0))))
        rcfg = RGBPreprocessConfig(size=ic.get("derive_rgb", {}).get("size", 256))
        tk, ts, rk, rs = [], [], [], []
        for r in samples.itertuples():
            d = derive_thermal(session, session.get(ThermalImage, r.thermal_image_id), tcfg)
            tk.append(d.object_key)
            ts.append(d.sha256)
            if isinstance(r.rgb_image_id, str):
                dr = derive_rgb(session, session.get(RGBImage, r.rgb_image_id), rcfg)
                rk.append(dr.object_key)
                rs.append(dr.sha256)
            else:
                rk.append(None)
                rs.append(None)
        samples["thermal_npy_key"], samples["thermal_npy_sha256"] = tk, ts
        samples["rgb_npy_key"], samples["rgb_npy_sha256"] = rk, rs
        session.commit()
        preprocessing = {"thermal": {"version": tcfg.version, **tcfg.__dict__},
                         "rgb": {"version": rcfg.version, **rcfg.__dict__}}
    else:
        preprocessing = {}

    # splits (patient level)
    pl = samples.groupby("patient_id").agg(has_event=("label", "max"), site_id=("site_id", "first")).reset_index()
    pl["study_id"] = pl["patient_id"].map(pat.set_index("id")["study_id"])
    sc = spec["split"]
    assign = patient_level_split(pl, sc["seed"], sc["test_fraction"], sc["val_fraction"], sc.get("stratify", []),
                                 [s for s in t["site"][t["site"]["site_code"].isin(sc.get("holdout_sites", []))]
                                  ["id"]])
    samples["split"] = samples["patient_id"].map(assign)
    samples["site_code"] = samples["site_id"].map(t["site"].set_index("id")["site_code"])
    assert_no_patient_leakage(samples)
    flow.add("ML samples (all splits)", patients=int(samples["patient_id"].nunique()),
             episodes=int(samples["episode_id"].nunique()), images=int(len(samples)))
    for sp, g in samples.groupby("split"):
        flow.add(f"  split={sp}", patients=int(g["patient_id"].nunique()), episodes=int(g["episode_id"].nunique()),
                 images=int(len(g)), note=f"positive samples {int(g['label'].sum())}; patients with event "
                                          f"{int(g.groupby('patient_id')['label'].max().sum())}")

    feature_cols = [c for c in samples.columns if c.split("__")[0] in ("clin", "base", "ep", "img")]
    ml_dir = root / "ml"
    files["ml/samples"] = _write(samples, ml_dir / "samples", csv=True)
    miss = missingness_report(samples, feature_cols)
    files["ml/missingness"] = _write(miss, ml_dir / "missingness", csv=True)
    cf = flow.to_frame()
    files["ml/cohort_flow"] = _write(cf, ml_dir / "cohort_flow", csv=True)
    (ml_dir / "cohort_flow.md").write_text(flow.to_markdown())
    feature_spec = {"feature_columns": feature_cols, "label": "label", "group": "patient_id", "time": "prediction_time",
                    "groups": {g: [c for c in feature_cols if c.startswith(g + "__")]
                               for g in ("clin", "base", "ep", "img")}}
    (ml_dir / "feature_spec.json").write_text(json.dumps(feature_spec, indent=2))
    (ml_dir / "splits.json").write_text(json.dumps({"assignment": assign.to_dict(),
                                                    "fingerprint": split_fingerprint(assign)}, indent=2))

    manifest = {
        "name": name, "version": version, "layer": "ml", "description": spec.get("description"),
        "created_time": utcnow().isoformat(), "data_cutoff_time": now.isoformat(),
        "code": code_version(), "data_dictionary_version": load_dictionary().version,
        "spec": spec, "spec_sha256": sha256_json(spec),
        "included_sites": t["site"][t["site"]["id"].isin(site_ids)]["site_code"].tolist(),
        "counts": {
            "patients": int(samples["patient_id"].nunique()), "episodes": int(samples["episode_id"].nunique()),
            "images": int(len(samples)), "rgb_paired_images": int(samples["rgb_image_id"].notna().sum()),
            "positive_samples": int(samples["label"].sum()),
            "episodes_with_event": int(tte[tte["episode_id"].isin(samples["episode_id"])]["event"].sum()),
            "longitudinal_episodes": int(len(tte)), "longitudinal_events": int(tte["event"].sum()),
        },
        "prevalence": {"sample_level": float(samples["label"].mean()) if len(samples) else None,
                       "patient_level": float(pl["has_event"].mean()) if len(pl) else None,
                       "by_split": samples.groupby("split")["label"].mean().to_dict()},
        "split_fingerprint": split_fingerprint(assign),
        "imaging_sources": {
            "xnat_enabled": get_settings().xnat_enabled, "imaging_primary": get_settings().imaging_primary,
            "images_in_xnat": int(samples["xnat_uri"].notna().sum()),
            "note": "original files are read via the ImagingService (object store / XNAT) and verified "
                    "against raw_sha256; training reads only the versioned derived arrays"},
        "preprocessing": preprocessing,
        "missingness_top": miss.head(15).to_dict("records"),
        "cohort_flow": flow.steps,
        "files": files,
    }
    manifest["checksum"] = sha256_json(files)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    for old in session.scalars(select(DatasetVersion).where(DatasetVersion.name == name,
                                                            DatasetVersion.version == version)).all():
        session.delete(old)        # unlocked rebuild of the same version replaces the registry entry
    session.flush()
    session.add(DatasetVersion(name=name, version=version, layer="ml", manifest=json.loads(
        json.dumps(manifest, default=str)), checksum=manifest["checksum"]))
    session.commit()
    manifest["root"] = str(root)
    return manifest


def longitudinal_thermal_features(samples: pd.DataFrame, history: pd.DataFrame, episodes: pd.DataFrame,
                                  baseline_hours: float, lookback_hours: float) -> pd.DataFrame:
    """Within-episode thermal change features using ONLY images acquired before the prediction time.

    * ``img__t_max_delta_baseline``: t_max minus the median t_max of earlier images taken in the first
      ``baseline_hours`` after insertion (the patient's own reference; removes inter-patient/camera offsets);
    * ``img__t_max_delta_prev``: change versus the most recent earlier image within ``lookback_hours``;
    * ``img__n_prior_images``.
    """
    h = history[["episode_id", "acquisition_time", "t_max"]].dropna().rename(columns={"acquisition_time": "h_time",
                                                                                    "t_max": "h_tmax"})
    s = samples[["episode_id", "prediction_time", "t_max"]].reset_index(names="_sidx")
    j = s.merge(h, on="episode_id")
    j = j[j["h_time"] < j["prediction_time"]]
    ins = j["episode_id"].map(episodes["insertion_time"])
    base = j[j["h_time"] <= ins + pd.Timedelta(hours=baseline_hours)].groupby("_sidx")["h_tmax"].median()
    recent = j[j["h_time"] >= j["prediction_time"] - pd.Timedelta(hours=lookback_hours)].sort_values("h_time")
    prev = recent.groupby("_sidx")["h_tmax"].last()
    out = pd.DataFrame(index=samples.index)
    out["img__t_max_delta_baseline"] = samples["t_max"] - base.reindex(samples.index)
    out["img__t_max_delta_prev"] = samples["t_max"] - prev.reindex(samples.index)
    out["img__n_prior_images"] = j.groupby("_sidx").size().reindex(samples.index).fillna(0).astype(float)
    out["_img_history_max_time"] = j.groupby("_sidx")["h_time"].max().reindex(samples.index)
    return out


def missingness_report(samples: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    rows = []
    for c in feature_cols:
        if c.endswith("__missing") or c.endswith("__count"):
            continue
        r = {"feature": c, "missing_overall": float(samples[c].isna().mean())}
        for sp, g in samples.groupby("split"):
            r[f"missing_{sp}"] = float(g[c].isna().mean())
        for site, g in samples.groupby("site_id"):
            r[f"missing_site_{site[:8]}"] = float(g[c].isna().mean())
        rows.append(r)
    return pd.DataFrame(rows).sort_values("missing_overall", ascending=False).reset_index(drop=True)


def lock_dataset(session: Session, name: str, version: str, out_root: str | Path | None = None) -> dict:
    """Freeze an ML dataset for the publication analysis (copy to locked/ and mark immutable)."""
    root = Path(out_root or get_settings().data_dir) / "datasets" / name / version
    dv = session.scalar(select(DatasetVersion).where(DatasetVersion.name == name, DatasetVersion.version == version))
    if dv is None:
        raise FileNotFoundError(f"dataset {name} v{version} not built")
    if dv.locked:
        raise LockedDatasetError("already locked")
    locked = root / "locked"
    shutil.copytree(root / "ml", locked)
    checks = {p.name: sha256_file(p) for p in sorted(locked.iterdir()) if p.is_file()}
    lock_info = {"locked_time": utcnow().isoformat(), "files": checks, "checksum": sha256_json(checks),
                 "code": code_version()}
    (locked / "LOCK.json").write_text(json.dumps(lock_info, indent=2))
    for p in locked.iterdir():
        p.chmod(0o444)
    dv.locked = True
    dv.manifest = {**dv.manifest, "lock": lock_info}
    session.commit()
    return lock_info


def verify_locked(root: str | Path) -> bool:
    locked = Path(root) / "locked"
    info = json.loads((locked / "LOCK.json").read_text())
    return all(sha256_file(locked / f) == h for f, h in info["files"].items())


def load_ml_dataset(root: str | Path) -> tuple[pd.DataFrame, dict, dict]:
    root = Path(root)
    layer = root / "locked" if (root / "locked").exists() else root / "ml"
    samples = pd.read_parquet(layer / "samples.parquet")
    spec = json.loads((layer / "feature_spec.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    return samples, spec, manifest


