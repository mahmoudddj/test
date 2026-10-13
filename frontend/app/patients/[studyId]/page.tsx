"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import Disclaimer from "@/components/Disclaimer";
import ThermalViewer from "@/components/ThermalViewer";
import Timeline from "@/components/Timeline";
import { api } from "@/lib/api";

export default function PatientTimeline() {
  const { studyId } = useParams<{ studyId: string }>();
  const [tl, setTl] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => { api(`/patients/${studyId}/timeline`).then(setTl).catch((e) => setErr(e.message)); }, [studyId]);
  const selected = useMemo(() => {
    if (!tl || !sel) return null;
    for (const ep of tl.episodes) {
      const im = ep.images.find((i: any) => i.id === sel);
      if (im) return { im, pred: (ep.predictions ?? []).filter((p: any) => p.thermal_image_id === sel).at(-1) ?? null };
    }
    return null;
  }, [tl, sel]);
  if (err) return <p className="error">{err}</p>;
  if (!tl) return <p className="muted">Loading...</p>;
  return (
    <>
      <h1>{tl.study_id}</h1>
      <Disclaimer />
      {!tl.prospective_alerts_enabled && <p className="muted">Mode: retrospective replay. Prospective research alerts are disabled until the model is validated and the study approval is recorded.</p>}
      {tl.episodes.map((ep: any) => (
        <div className="panel" key={ep.id}>
          <h2>{ep.episode_code} - {ep.catheter_type} ({ep.insertion_site}{ep.tunnelled ? ", tunnelled" : ""})</h2>
          <p className="muted">Inserted {ep.insertion_time}; {ep.removal_time ? `removed ${ep.removal_time} (${ep.removal_reason ?? "-"})` : "in situ / end of follow-up " + (ep.end_of_follow_up_time ?? "-")}.
            {" "}Reference outcome: <strong>{ep.outcome ? `${ep.outcome.outcome_type} (${ep.outcome.status})` : "not yet available"}</strong></p>
          <Timeline episode={ep} observations={tl.observations} antibiotics={tl.antibiotics} microbiology={tl.microbiology} selected={sel} onSelect={setSel} />
        </div>
      ))}
      {selected && <ThermalViewer image={selected.im} prediction={selected.pred} />}
    </>
  );
}
