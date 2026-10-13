"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/* eslint-disable @typescript-eslint/no-explicit-any */
type DQ = any;

function Kpi({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return <div className="panel kpi"><div className="l">{label}</div><div className="v">{value}</div>{sub && <div className="muted">{sub}</div>}</div>;
}

function Bar({ frac }: { frac: number | null }) {
  const f = frac == null || !isFinite(frac) ? 0 : Math.max(0, Math.min(1, frac));
  const color = f >= 0.9 ? "var(--pass)" : f >= 0.75 ? "var(--warn)" : "var(--fail)";
  return <div className="bar-bg"><div className="bar" style={{ width: `${f * 100}%`, background: color }} /></div>;
}

const pct = (x: number | null | undefined) => (x == null ? "-" : `${(100 * x).toFixed(0)}%`);

export default function DataQuality() {
  const [dq, setDq] = useState<DQ | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<DQ>("/dq/summary").then(setDq).catch((e) => setErr(e.message)); }, []);
  if (err) return <p className="error">{err}</p>;
  if (!dq) return <p className="muted">Loading data-quality metrics...</p>;
  const im = dq.imaging;
  const qc = dq.qc.thermal_image.by_status ?? {};
  const qcTotal = Object.values(qc).reduce((a: number, b: any) => a + (b as number), 0) as number;
  const sites: Record<string, string> = Object.fromEntries(dq.sites.map((s: any) => [s.site_code, s.site_id]));
  return (
    <>
      <h1>Study monitoring &amp; data quality</h1>
      <p className="muted">Generated {dq.generated_time}. Drill-down shows pseudonymous Study IDs only.</p>
      <div className="grid">
        <Kpi label="Enrolled" value={dq.recruitment.enrolled_total} sub={`withdrawn ${dq.recruitment.withdrawn}`} />
        <Kpi label="Catheter episodes" value={dq.episodes.total} sub={Object.entries(dq.episodes.by_catheter_type).map(([k, v]) => `${k} ${v}`).join(" | ")} />
        <Kpi label="Imaging timepoints" value={`${im.collected_timepoints}/${im.expected_timepoints}`} sub={`${pct(im.collected_timepoints / Math.max(im.expected_timepoints, 1))} complete`} />
        <Kpi label="Thermal QC FAIL" value={qc.FAIL ?? 0} sub={`${pct((qc.FAIL ?? 0) / Math.max(qcTotal, 1))} of ${qcTotal} | WARN ${qc.WARNING ?? 0}`} />
        <Kpi label="Open queries" value={dq.queries.open} sub={`median age ${dq.queries.median_age_days?.toFixed?.(1) ?? "-"} d`} />
        <Kpi label="Missing outcome labels" value={dq.outcomes.missing_outcome_label.length} sub={`provisional ${dq.outcomes.provisional}`} />
        <Kpi label="Events / prevalence" value={dq.outcomes.events} sub={`episode prevalence ${pct(dq.outcomes.event_prevalence_by_episode)}`} />
        <Kpi label="Annotation" value={`${dq.annotation.both_done}/${dq.annotation.eligible_images}`} sub={`double-read; adjudicated ${dq.annotation.adjudicated_images}`} />
        <Kpi label="Protocol deviations" value={dq.deviations.total} sub={`open ${dq.deviations.open}`} />
        <Kpi label="Flagged values" value={dq.value_checks.flagged_values} sub={`import rejections ${dq.value_checks.import_rejected_rows}`} />
        <Kpi label="Duplicates" value={dq.duplicates.duplicate_images} sub={`images | ${dq.duplicates.duplicate_observations} observations`} />
      </div>

      <div className="row">
        <div className="panel" style={{ flex: 1, minWidth: 320 }}>
          <h2>Recruitment by centre</h2>
          <table><thead><tr><th>site</th><th>screened</th><th>enrolled</th><th>episodes</th><th>report</th></tr></thead><tbody>
            {Object.keys(sites).map((sc) => (
              <tr key={sc}><td>{sc}</td><td>{dq.recruitment.screened_by_site[sc] ?? 0}</td><td>{dq.recruitment.by_site[sc] ?? 0}</td>
                <td>{dq.episodes.by_site[sc] ?? 0}</td>
                <td><SiteReportLink site={sc} /></td></tr>))}
          </tbody></table>
        </div>
        <div className="panel" style={{ flex: 1, minWidth: 320 }}>
          <h2>Imaging completeness by centre</h2>
          <table><tbody>{im.by_site.map((r: any) => (
            <tr key={r.site_code}><td style={{ width: 60 }}>{r.site_code}</td><td><Bar frac={r.completeness} /></td><td style={{ width: 110 }}>{r.collected}/{r.expected} ({pct(r.completeness)})</td></tr>))}
          </tbody></table>
        </div>
      </div>

      <div className="panel">
        <h2>Required clinical variables - completeness</h2>
        <div className="scroll"><table><thead><tr><th>site</th><th>variable</th><th style={{ width: "40%" }}></th><th>observed/expected</th></tr></thead><tbody>
          {dq.clinical_completeness.map((r: any, i: number) => (
            <tr key={i}><td>{r.site_code}</td><td>{r.variable}</td><td><Bar frac={r.completeness} /></td><td>{r.observed}/{r.expected} ({pct(r.completeness)})</td></tr>))}
        </tbody></table></div>
      </div>

      <div className="row">
        <div className="panel" style={{ flex: 1, minWidth: 320 }}>
          <h2>Top QC reasons (thermal)</h2>
          <table><tbody>{Object.entries(dq.qc.thermal_image.top_reasons).map(([k, v]) => {
            const [check, sev] = k.split(":");
            return <tr key={k}><td>{check}</td><td className={sev}>{sev}</td><td>{v as number}</td></tr>;
          })}</tbody></table>
        </div>
        <div className="panel" style={{ flex: 1, minWidth: 320 }}>
          <h2>Cross-site comparison</h2>
          <div className="scroll"><table><thead><tr><th>site</th><th>images</th><th>QC fail</th><th>median T mean</th><th>median ambient</th><th>median distance</th><th>missing ambient</th><th>cameras</th></tr></thead><tbody>
            {dq.cross_site.map((r: any) => (
              <tr key={r.site_code}><td>{r.site_code}</td><td>{r.n_images}</td><td>{pct(r.qc_fail_rate)}</td><td>{r.median_t_mean?.toFixed(2)}</td>
                <td>{r.median_ambient?.toFixed(1)}</td><td>{r.median_distance_cm?.toFixed(0)}</td><td>{pct(r.share_missing_ambient)}</td><td>{r.camera_models.join(", ")}</td></tr>))}
          </tbody></table></div>
        </div>
      </div>

      <div className="row">
        <div className="panel" style={{ flex: 1, minWidth: 320 }}>
          <h2>Missing imaging timepoints ({im.missing_timepoints.length})</h2>
          <div className="scroll" style={{ maxHeight: 320, overflowY: "auto" }}><table><thead><tr><th>study ID</th><th>episode</th><th>slot start (UTC)</th></tr></thead><tbody>
            {im.missing_timepoints.slice(0, 300).map((r: any, i: number) => (
              <tr key={i}><td><Link href={`/patients/${r.study_id}`}>{r.study_id}</Link></td><td>{r.episode_code}</td><td>{r.slot_start}</td></tr>))}
          </tbody></table></div>
        </div>
        <div className="panel" style={{ flex: 1, minWidth: 320 }}>
          <h2>Data latency (event to database)</h2>
          <table><thead><tr><th>table</th><th>site</th><th>median h</th><th>p90 h</th></tr></thead><tbody>
            {dq.data_latency.map((r: any, i: number) => (<tr key={i}><td>{r.table}</td><td>{r.site_code}</td><td>{r.median_hours.toFixed(1)}</td><td>{r.p90_hours.toFixed(1)}</td></tr>))}
          </tbody></table>
          <h2>Closed episodes without outcome label</h2>
          <table><tbody>{dq.outcomes.missing_outcome_label.map((r: any) => (
            <tr key={r.episode_code}><td><Link href={`/patients/${r.study_id}`}>{r.study_id}</Link></td><td>{r.episode_code}</td><td>{r.window_end}</td></tr>))}
          </tbody></table>
        </div>
      </div>
    </>
  );
}

function SiteReportLink({ site }: { site: string }) {
  const month = new Date().toISOString().slice(0, 7);
  async function open() {
    const html = await api<string>(`/dq/site-report/${site}?month=${month}`);
    const w = window.open("", "_blank");
    if (w) { w.document.write(html); w.document.close(); }
  }
  return <button className="secondary" onClick={open}>{month} report</button>;
}
