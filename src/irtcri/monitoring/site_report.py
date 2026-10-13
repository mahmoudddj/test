"""Monthly site-level QC report (Markdown / HTML)."""

from __future__ import annotations

import html

import pandas as pd

from irtcri import INVESTIGATIONAL_DISCLAIMER
from irtcri.monitoring.dq import compute_dq


def _month_bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(month + "-01", tz="UTC")
    return start, start + pd.offsets.MonthBegin(1)


def site_report_markdown(t: dict[str, pd.DataFrame], site_code: str, month: str) -> str:
    start, end = _month_bounds(month)
    dq = compute_dq(t, now=min(end, pd.Timestamp.now(tz="UTC")))
    pat = t["patient"]
    new = pat[(pd.to_datetime(pat["enrolment_date"]) >= start.tz_localize(None)) &
              (pd.to_datetime(pat["enrolment_date"]) < end.tz_localize(None))] if len(pat) else pat
    th = t["thermal_image"]
    th_m = th[(th["acquisition_time"] >= start) & (th["acquisition_time"] < end)] if len(th) else th
    im = dq["imaging"]
    lines = [
        f"# Site QC report - {site_code} - {month}", "",
        f"_Generated {dq['generated_time']}. {INVESTIGATIONAL_DISCLAIMER}_", "",
        "## Recruitment", "",
        f"* Enrolled this month: **{len(new)}**; cumulative: **{dq['recruitment']['enrolled_total']}**",
        f"* Screened (cumulative): {sum(dq['recruitment']['screened_by_site'].values())}",
        f"* Withdrawn (cumulative): {dq['recruitment']['withdrawn']}",
        f"* Catheter episodes (cumulative): {dq['episodes']['total']} {dq['episodes']['by_catheter_type']}", "",
        "## Imaging", "",
        f"* Thermal images acquired this month: **{len(th_m)}**",
        f"* Timepoints collected/expected (cumulative): {im['collected_timepoints']}/{im['expected_timepoints']}",
        f"* Missing imaging timepoints: {len(im['missing_timepoints'])}",
        f"* QC this month: {th_m['qc_status'].value_counts().to_dict() if len(th_m) else {}}",
        f"* Top QC reasons (cumulative): {dq['qc']['thermal_image']['top_reasons']}", "",
        "## Clinical data completeness (required repeated variables)", "",
        "| variable | observed | expected | completeness |", "|---|---|---|---|",
    ]
    for r in dq["clinical_completeness"]:
        c = f"{r['completeness']:.0%}" if r["completeness"] is not None else "-"
        lines.append(f"| {r['variable']} | {r['observed']} | {r['expected']} | {c} |")
    lines += ["", "## Outcomes and adjudication", "",
              f"* Closed episodes without outcome label: {len(dq['outcomes']['missing_outcome_label'])}",
              f"* Provisional (not yet adjudicated) outcomes: {dq['outcomes']['provisional']}",
              f"* Annotation progress: {dq['annotation']}", "",
              "## Queries, deviations, value checks", "",
              f"* Open data queries: **{dq['queries']['open']}** (median age {dq['queries']['median_age_days']} days)",
              f"* Protocol deviations: {dq['deviations']['total']} (open {dq['deviations']['open']}) "
              f"{dq['deviations']['by_category']}",
              f"* Flagged (implausible/cross-field) values: {dq['value_checks']['flagged_values']}",
              f"* Rejected import rows: {dq['value_checks']['import_rejected_rows']}",
              f"* Duplicate images: {dq['duplicates']['duplicate_images']}; duplicate observations: "
              f"{dq['duplicates']['duplicate_observations']}", "",
              "## Data latency (hours from event to database)", "",
              "| table | median h | p90 h |", "|---|---|---|"]
    for r in dq["data_latency"]:
        lines.append(f"| {r['table']} | {r['median_hours']:.1f} | {r['p90_hours']:.1f} |")
    lines += ["", "## Missing imaging timepoints (first 50)", "",
              "| study_id | episode | slot start |", "|---|---|---|"]
    for r in im["missing_timepoints"][:50]:
        lines.append(f"| {r['study_id']} | {r['episode_code']} | {r['slot_start']} |")
    return "\n".join(lines) + "\n"


def site_report_html(t: dict[str, pd.DataFrame], site_code: str, month: str) -> str:
    md = site_report_markdown(t, site_code, month)
    body, in_table = [], False
    for line in md.splitlines():
        if line.startswith("|"):
            cells = [html.escape(c.strip()) for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-"):
                continue
            if not in_table:
                body.append("<table>")
                in_table = True
                body.append("<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>")
            else:
                body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            body.append("</table>")
            in_table = False
        if line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("* "):
            body.append(f"<li>{html.escape(line[2:]).replace('**', '')}</li>")
        elif line.strip():
            body.append(f"<p>{html.escape(line.strip('_'))}</p>")
    if in_table:
        body.append("</table>")
    css = ("body{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#1f2937}"
           "table{border-collapse:collapse;margin:.5rem 0}td,th{border:1px solid #d1d5db;padding:.25rem .5rem;"
           "font-size:.9rem}th{background:#f3f4f6}h2{margin-top:1.5rem;border-bottom:1px solid #e5e7eb}")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Site QC {html.escape(site_code)} {month}"
            f"</title><style>{css}</style></head><body>{''.join(body)}</body></html>")
