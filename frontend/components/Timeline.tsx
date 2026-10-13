"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useMemo } from "react";

const LANES = ["Events", "Thermal images", "Exit-site assessment", "Temperature (degC)", "Antibiotics", "Microbiology", "Model risk (research)"];
const LANE_H = 34;
const LEFT = 150;

export default function Timeline({ episode, observations, antibiotics, microbiology, selected, onSelect }: {
  episode: any; observations: any[]; antibiotics: any[]; microbiology: any[];
  selected: string | null; onSelect: (imageId: string) => void;
}) {
  const t0 = Date.parse(episode.insertion_time);
  const endIso = episode.removal_time ?? episode.end_of_follow_up_time ??
    episode.images.at(-1)?.time ?? new Date(t0 + 7 * 864e5).toISOString();
  const t1 = Math.max(Date.parse(endIso), t0 + 864e5);
  const W = 1000;
  const x = (iso: string) => LEFT + ((Date.parse(iso) - t0) / (t1 - t0)) * (W - LEFT - 10);
  const lane = (i: number) => 20 + i * LANE_H;
  const temps = useMemo(() => observations.filter((o) => o.variable === "temperature_c" && Date.parse(o.time) >= t0 && Date.parse(o.time) <= t1)
    .sort((a, b) => Date.parse(a.time) - Date.parse(b.time)), [observations, t0, t1]);
  const preds = episode.predictions ?? [];
  const thr = preds[0]?.threshold ?? null;
  const yTemp = (v: number) => lane(3) + LANE_H / 2 - ((v - 37) / 3) * (LANE_H - 6);
  const yProb = (p: number) => lane(6) + LANE_H - 4 - p * (LANE_H - 8);
  const days = Math.ceil((t1 - t0) / 864e5);
  const oc = episode.outcome;
  return (
    <div className="scroll">
      <svg viewBox={`0 0 ${W} ${lane(LANES.length) + 24}`} style={{ width: "100%", minWidth: 700 }} role="img"
        aria-label={`Timeline of catheter episode ${episode.episode_code}`}>
        {LANES.map((l, i) => (
          <g key={l}><text x={4} y={lane(i) + LANE_H / 2 + 4} fontSize={11} fill="currentColor" opacity={0.75}>{l}</text>
            <line x1={LEFT} x2={W - 10} y1={lane(i) + LANE_H} y2={lane(i) + LANE_H} stroke="var(--border)" /></g>))}
        {Array.from({ length: days + 1 }, (_, d) => {
          const xx = LEFT + (d * 864e5 / (t1 - t0)) * (W - LEFT - 10);
          return xx <= W - 10 ? <g key={d}><line x1={xx} x2={xx} y1={14} y2={lane(LANES.length)} stroke="var(--border)" strokeDasharray="2 3" />
            <text x={xx + 2} y={lane(LANES.length) + 14} fontSize={10} fill="currentColor" opacity={0.6}>day {d}</text></g> : null;
        })}
        {/* events */}
        <line x1={x(episode.insertion_time)} x2={x(episode.insertion_time)} y1={lane(0)} y2={lane(LANES.length)} stroke="var(--accent)" strokeWidth={2} />
        <text x={x(episode.insertion_time) + 3} y={lane(0) + 12} fontSize={10} fill="var(--accent)">insertion</text>
        {episode.removal_time && <><line x1={x(episode.removal_time)} x2={x(episode.removal_time)} y1={lane(0)} y2={lane(LANES.length)} stroke="var(--muted)" strokeWidth={2} />
          <text x={x(episode.removal_time) - 44} y={lane(0) + 12} fontSize={10} fill="currentColor">removal</text></>}
        {oc?.onset_time_reference && <line x1={x(oc.onset_time_reference)} x2={x(oc.onset_time_reference)} y1={lane(0)} y2={lane(LANES.length)} stroke="var(--fail)" strokeDasharray="4 3" />}
        {oc?.clinical_recognition_time && <><line x1={x(oc.clinical_recognition_time)} x2={x(oc.clinical_recognition_time)} y1={lane(0)} y2={lane(LANES.length)} stroke="var(--fail)" strokeWidth={2} />
          <text x={x(oc.clinical_recognition_time) + 3} y={lane(0) + 26} fontSize={10} fill="var(--fail)">clinical recognition ({oc.outcome_type})</text></>}
        {/* images */}
        {episode.images.map((im: any) => (
          <circle key={im.id} cx={x(im.time)} cy={lane(1) + LANE_H / 2} r={selected === im.id ? 7 : 5} style={{ cursor: "pointer" }}
            fill={im.qc_status === "FAIL" ? "var(--fail)" : im.qc_status === "WARNING" ? "var(--warn)" : "var(--pass)"}
            stroke={selected === im.id ? "currentColor" : "none"} strokeWidth={2} onClick={() => onSelect(im.id)}>
            <title>{`${im.time} QC ${im.qc_status}${im.t_max ? ` Tmax ${im.t_max.toFixed(1)}` : ""}${im.reference_label ? ` ref: ${im.reference_label}` : ""}`}</title>
          </circle>))}
        {/* assessments */}
        {episode.assessments.map((a: any, i: number) => (
          <rect key={i} x={x(a.time) - 4} y={lane(2) + LANE_H / 2 - 4} width={8} height={8}
            fill={a.impression === "confirmed_infection" ? "var(--fail)" : a.impression === "suspected_infection" ? "var(--warn)" : "var(--muted)"}>
            <title>{`${a.time}: ${a.impression}, erythema ${a.erythema_mm ?? "-"} mm${a.purulent_discharge ? ", purulent" : ""}`}</title></rect>))}
        {/* temperature */}
        <polyline fill="none" stroke="var(--accent)" strokeWidth={1.2} points={temps.map((o) => `${x(o.time)},${yTemp(o.value)}`).join(" ")} />
        {/* antibiotics */}
        {antibiotics.map((m: any, i: number) => (
          <rect key={i} x={x(m.start)} y={lane(4) + 10} height={12} rx={3} fill="var(--warn)" opacity={0.8}
            width={Math.max(4, x(m.end && Date.parse(m.end) < t1 ? m.end : new Date(t1).toISOString()) - x(m.start))}>
            <title>{`${m.drug} ${m.route ?? ""} from ${m.start}`}</title></rect>))}
        {/* microbiology */}
        {microbiology.map((m: any, i: number) => (
          <path key={i} d={`M ${x(m.time)} ${lane(5) + 8} l 6 12 l -12 0 z`} fill={m.growth === "positive" ? "var(--fail)" : "var(--muted)"}>
            <title>{`${m.specimen} collected ${m.time}: ${m.growth} ${m.organism ?? ""} (reported ${m.reported ?? "-"})`}</title></path>))}
        {/* predictions */}
        {thr != null && <line x1={LEFT} x2={W - 10} y1={yProb(thr)} y2={yProb(thr)} stroke="var(--fail)" strokeDasharray="3 3" />}
        <polyline fill="none" stroke="currentColor" strokeWidth={1} opacity={0.6} points={preds.map((p: any) => `${x(p.time)},${yProb(p.probability)}`).join(" ")} />
        {preds.map((p: any) => (
          <circle key={p.id} cx={x(p.time)} cy={yProb(p.probability)} r={3} fill={p.elevated_risk ? "var(--fail)" : "currentColor"}
            style={{ cursor: p.thermal_image_id ? "pointer" : "default" }} onClick={() => p.thermal_image_id && onSelect(p.thermal_image_id)}>
            <title>{`${p.time}: p=${p.probability} (${p.model_version}, ${p.mode})`}</title></circle>))}
      </svg>
    </div>
  );
}
