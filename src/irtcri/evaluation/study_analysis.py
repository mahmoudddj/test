"""Study-level statistical analyses on a built dataset (descriptive + inferential).

Produces publication-ready tables (CSV + Markdown) and figures:
* Table 1: baseline characteristics by episode outcome (patient/episode level);
* Kaplan-Meier cumulative incidence of CRI/ESI recognition (overall, by catheter type) + log-rank;
* Cox PH model (cluster-robust by patient) for time to recognition;
* GEE logistic model relating thermal features to image-level label (repeated measures);
* linear mixed model of thermal delta (t_max - ambient) trajectory before recognition.
Model specifications are illustrative defaults; the final SAP must pre-specify them.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from irtcri.core.tables import to_markdown
from irtcri.evaluation import plots
from irtcri.evaluation.clustered import gee_logistic, mixed_linear
from irtcri.evaluation.survival import cox_ph, kaplan_meier, logrank_test, median_survival
from irtcri.monitoring.dq import json_safe


def table1(patients: pd.DataFrame, tte: pd.DataFrame) -> pd.DataFrame:
    ep = tte.merge(patients.rename(columns={"id": "patient_id"}), on="patient_id", how="left")
    ep["group"] = np.where(ep["event"] == 1, "CRI/ESI", "No CRI/ESI")
    rows = []

    def cont(var, label):
        r = {"characteristic": label}
        for g, d in [("All", ep)] + list(ep.groupby("group")):
            v = d[var].dropna()
            r[g] = f"{v.median():.1f} [{v.quantile(.25):.1f}-{v.quantile(.75):.1f}] (n={len(v)})" if len(v) else "-"
        rows.append(r)

    def binary(var, label):
        r = {"characteristic": label}
        for g, d in [("All", ep)] + list(ep.groupby("group")):
            v = d[var].dropna().astype(bool)
            r[g] = f"{int(v.sum())}/{len(v)} ({100 * v.mean():.0f}%)" if len(v) else "-"
        rows.append(r)

    rows.append({"characteristic": "Catheter episodes, n", "All": str(len(ep)),
                 **{g: str(len(d)) for g, d in ep.groupby("group")}})
    cont("age_years", "Age, years, median [IQR]")
    ep["female"] = ep["sex"] == "female"
    binary("female", "Female sex")
    cont("bmi", "BMI, kg/m2")
    comorb = (("diabetes", "Diabetes"), ("on_dialysis", "Haemodialysis"), ("immunosuppressed", "Immunosuppressed"))
    for v, lab in comorb:
        binary(v, lab)
    for ct in sorted(ep["catheter_type"].dropna().unique()):
        ep[f"ct_{ct}"] = ep["catheter_type"] == ct
        binary(f"ct_{ct}", f"Catheter type {ct}")
    cont("duration_h", "Follow-up, hours")
    return pd.DataFrame(rows)


def run_study_analysis(dataset_root: str | Path, out_dir: str | Path) -> dict:
    root, out = Path(dataset_root), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tte = pd.read_parquet(root / "longitudinal" / "time_to_event.parquet")
    pats = pd.read_parquet(root / "validated" / "patients.parquet")
    samples = pd.read_parquet(root / "ml" / "samples.parquet")
    res: dict = {}
    t1 = table1(pats, tte)
    t1.to_csv(out / "table1.csv", index=False)
    res["table1_markdown"] = to_markdown(t1)
    (out / "table1.md").write_text(res["table1_markdown"])

    km_all = kaplan_meier(tte["duration_h"], tte["event"])
    curves = {"All episodes": km_all}
    for ct, d in tte.groupby("catheter_type"):
        if len(d) >= 5:
            curves[ct] = kaplan_meier(d["duration_h"], d["event"])
    res["km_median_h"] = median_survival(km_all)
    res["km_figure"] = plots.km_plot(curves, out / "fig_km")
    km_all.to_csv(out / "km_overall.csv", index=False)
    if tte["catheter_type"].nunique() > 1 and tte["event"].sum() > 0:
        res["logrank_catheter_type"] = logrank_test(tte["duration_h"], tte["event"], tte["catheter_type"])
    try:
        cov = tte.merge(pats.rename(columns={"id": "patient_id"})[["patient_id", "diabetes", "immunosuppressed"]],
                        on="patient_id", how="left")
        cov[["diabetes", "immunosuppressed"]] = cov[["diabetes", "immunosuppressed"]].astype(float).fillna(0)
        cx = cox_ph(cov[cov["duration_h"] > 0], "duration_h", "event", ["diabetes", "immunosuppressed"])
        cx.to_csv(out / "cox.csv", index=False)
        res["cox"] = cx.to_dict("records")
    except Exception as e:  # too few events etc.
        res["cox"] = {"error": str(e)}
    s = samples.dropna(subset=["img__t_max_minus_ambient"]).copy()
    try:
        g = gee_logistic(s, "label ~ img__t_max_minus_ambient + ep__hours_since_insertion", "patient_id")
        g.to_csv(out / "gee_thermal_label.csv", index=False)
        res["gee"] = g.to_dict("records")
    except Exception as e:
        res["gee"] = {"error": str(e)}
    ev = s[s["hours_to_recognition"].notna()].copy()
    if len(ev) > 10 and ev["patient_id"].nunique() > 2:
        ev["hours_before_recognition"] = ev["hours_to_recognition"]
        try:
            mm = mixed_linear(ev, "img__t_max_minus_ambient ~ hours_before_recognition", "patient_id")
            mm.to_csv(out / "mixed_thermal_trajectory.csv", index=False)
            res["mixed_model"] = mm.to_dict("records")
        except Exception as e:
            res["mixed_model"] = {"error": str(e)}
    res = json_safe(res)
    (out / "study_analysis.json").write_text(json.dumps(res, indent=2))
    return res
