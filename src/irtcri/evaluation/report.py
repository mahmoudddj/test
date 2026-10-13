"""Assemble model-evaluation reports (tables + figures + markdown/HTML)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from irtcri import INVESTIGATIONAL_DISCLAIMER
from irtcri.evaluation import plots
from irtcri.evaluation.calibration import calibration_intercept_slope, expected_calibration_error
from irtcri.evaluation.early_warning import AlertRule, evaluate_early_warning, lead_time_distribution
from irtcri.evaluation.metrics import metrics_with_ci, patient_level, threshold_table
from irtcri.evaluation.subgroups import stratified
from irtcri.monitoring.dq import json_safe


def _fmt(d: dict) -> str:
    if d.get("ci_low") is None:
        return f"{d['estimate']:.3f}" if isinstance(d.get("estimate"), float) else str(d.get("estimate"))
    return f"{d['estimate']:.3f} ({d['ci_low']:.3f}-{d['ci_high']:.3f})"


def evaluate_predictions(pred: pd.DataFrame, episodes: pd.DataFrame, threshold: float, out_dir: str | Path,
                         title: str, split_name: str, n_boot: int = 1000, alert_rule: AlertRule | None = None,
                         seed: int = 0) -> dict:
    """``pred`` needs: patient_id, episode_id, site_id, catheter_type, prediction_time, label, prob.

    ``episodes`` needs the columns described in ``early_warning.episode_alerts``.
    The threshold MUST come from the validation set (never chosen on the evaluated data).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    res: dict = {"title": title, "split": split_name, "threshold": threshold, "disclaimer": INVESTIGATIONAL_DISCLAIMER}
    y, p = pred["label"].to_numpy(), pred["prob"].to_numpy()
    res["image_level"] = metrics_with_ci(pred, threshold, n_boot=n_boot, seed=seed)
    pl = patient_level(pred, group="episode_id").merge(pred[["episode_id", "patient_id"]].drop_duplicates(),
                                                       on="episode_id")
    res["episode_level_max_score"] = metrics_with_ci(pl, threshold, n_boot=n_boot, seed=seed)
    res["calibration"] = {**calibration_intercept_slope(y, p), "ece": expected_calibration_error(y, p)}
    threshold_table(y, p).to_csv(out / "threshold_table.csv", index=False)
    rule = alert_rule or AlertRule(threshold=threshold)
    ew = evaluate_early_warning(pred[["episode_id", "prediction_time", "prob"]], episodes, rule, n_boot=n_boot,
                                seed=seed)
    res["early_warning"] = {"rule": ew["rule"], "summary": ew["summary"]}
    ew["per_episode"].to_csv(out / "early_warning_per_episode.csv", index=False)
    figs = []
    if len(np.unique(y)) == 2:
        figs += plots.roc_pr(y, p, out / "fig_roc_pr", threshold=threshold)
        figs += plots.calibration_plot(y, p, out / "fig_calibration")
    figs += plots.lead_time_histogram(lead_time_distribution(ew["per_episode"]), out / "fig_lead_time")
    res["figures"] = figs

    def _sg(g: pd.DataFrame) -> dict:
        from irtcri.evaluation.metrics import binary_metrics
        m = binary_metrics(g["label"], g["prob"], threshold)
        return {k: m[k] for k in ("sensitivity", "specificity", "ppv", "auroc")}

    site_col = "site_code" if "site_code" in pred else "site_id"
    sub = pd.concat([stratified(pred, site_col, _sg, "secondary"),
                     stratified(pred, "catheter_type", _sg, "secondary")], ignore_index=True)
    sub.to_csv(out / "subgroups.csv", index=False)
    res["subgroups"] = sub.to_dict("records")
    res = json_safe(res)
    (out / "evaluation.json").write_text(json.dumps(res, indent=2))
    (out / "evaluation.md").write_text(render_markdown(res))
    return res


def render_markdown(res: dict) -> str:
    il, el = res["image_level"], res["episode_level_max_score"]
    ew = res["early_warning"]["summary"]
    keys = ("sensitivity", "specificity", "ppv", "npv", "f1", "auroc", "auprc", "brier")
    lines = [f"# {res['title']} - {res['split']} set", "", f"_{res['disclaimer']}_", "",
             f"Operating threshold **{res['threshold']:.3f}** (selected on the validation set).", "",
             "95% CIs: percentile bootstrap resampling **patients** (images within a patient are correlated).", "",
             "| metric | image-level | episode-level (max score) |", "|---|---|---|"]
    for k in keys:
        lines.append(f"| {k} | {_fmt(il[k])} | {_fmt(el[k])} |")
    c = il["counts"]
    lines += ["", f"Image level: n={c['n']} images from {c['n_clusters']} patients; TP={c['tp']} FP={c['fp']} "
                  f"TN={c['tn']} FN={c['fn']}.", "", "## Calibration", "",
              f"slope {res['calibration'].get('slope')}, "
              f"calibration-in-the-large {res['calibration'].get('intercept_citl')},"
              f" ECE {res['calibration'].get('ece')}", "", "## Early-warning performance (episode/event level)", "",
              f"Alert rule: `{res['early_warning']['rule']}`", "", "| metric | estimate (95% CI) |", "|---|---|"]
    for k, v in ew.items():
        lines.append(f"| {k} | {_fmt(v)} |")
    lines += ["", "## Subgroups (secondary; small strata are unstable)", "",
              "| variable | level | n | events | sensitivity | specificity | AUROC |", "|---|---|---|---|---|---|---|"]
    for r in res["subgroups"]:
        f = lambda v: "-" if v is None else f"{v:.2f}"  # noqa: E731
        lines.append(f"| {r['subgroup_variable']} | {r['level']} | {r['n']} | {r['n_events']} | "
                     f"{f(r.get('sensitivity'))} | {f(r.get('specificity'))} | {f(r.get('auroc'))} |")
    lines += ["", "Interpretation note: project KPIs (>= 6 h earlier detection, precision/recall >= 0.85) are "
                  "targets; they were not used to select thresholds or models."]
    return "\n".join(lines) + "\n"
