"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import AnnotationCanvas, { type Shapes } from "@/components/AnnotationCanvas";
import AuthedImage from "@/components/AuthedImage";
import { api } from "@/lib/api";

const LABELS: [string, string][] = [
  ["no_infection", "No infection"], ["possible_infection", "Possible infection"],
  ["exit_site_infection", "Exit-site infection"], ["tunnel_infection", "Tunnel infection"],
  ["pocket_infection", "Port pocket infection"], ["crbsi", "CRBSI (where applicable)"],
  ["uncertain_insufficient_data", "Uncertain / insufficient data"],
];

export default function AnnotateTask() {
  const { imageId } = useParams<{ imageId: string }>();
  const router = useRouter();
  const [task, setTask] = useState<any | null>(null);
  const [shapes, setShapes] = useState<Shapes>({ insertion_point: null, roi: [], reference_roi: [] });
  const [label, setLabel] = useState<string | null>(null);
  const [confidence, setConfidence] = useState<number>(3);
  const [quality, setQuality] = useState<boolean>(true);
  const [comment, setComment] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    api<any>(`/annotation/tasks/${imageId}`).then((t) => {
      setTask(t);
      const a = t.my_annotation;
      if (a) {
        setShapes({ insertion_point: a.insertion_point, roi: a.roi?.points ?? [], reference_roi: a.reference_roi?.points ?? [] });
        setLabel(a.label); setConfidence(a.confidence ?? 3); setQuality(a.image_quality_adequate ?? true); setComment(a.comment ?? "");
      }
    }).catch((e) => setMsg(e.message));
  }, [imageId]);
  async function save(submit: boolean) {
    setMsg(null);
    try {
      await api(`/annotation/tasks/${imageId}`, { method: "PUT", body: JSON.stringify({
        insertion_point: shapes.insertion_point,
        roi: shapes.roi.length > 2 ? { type: "polygon", points: shapes.roi } : null,
        reference_roi: shapes.reference_roi.length > 2 ? { type: "polygon", points: shapes.reference_roi } : null,
        label, confidence, image_quality_adequate: quality, comment: comment || null, submit }) });
      setMsg(submit ? "Submitted." : "Draft saved.");
      if (submit) router.push("/annotate");
    } catch (e) { setMsg((e as Error).message); }
  }
  if (!task) return <p className="muted">{msg ?? "Loading task..."}</p>;
  const submitted = task.my_annotation?.status === "submitted";
  return (
    <>
      <h1>Task {task.task_code} <span className="muted">({task.reader_role}, profile: {task.blinding_profile})</span></h1>
      <div className="row">
        <div className="panel" style={{ flex: 2, minWidth: 320 }}>
          <AnnotationCanvas imagePath={`/images/thermal/${imageId}/render.png`} width={task.width ?? 160} height={task.height ?? 120}
            value={shapes} onChange={setShapes} />
          {task.rgb_image_id && <><h3>Paired RGB</h3><AuthedImage path={`/images/rgb/${task.rgb_image_id}/render.png`} alt="RGB" style={{ maxWidth: 480, imageRendering: "auto" }} /></>}
        </div>
        <div className="panel" style={{ flex: 1, minWidth: 280 }}>
          {task.catheter_type && <p>Catheter: <strong>{task.catheter_type}</strong>, {task.insertion_site}{task.tunnelled ? ", tunnelled" : ""}</p>}
          {task.hours_since_insertion != null && <p>Time since insertion: {task.hours_since_insertion} h</p>}
          {task.clinical_context && <details open><summary>Clinical data available before acquisition</summary>
            <table><tbody>{task.clinical_context.map((c: any, i: number) => (
              <tr key={i}><td>{c.variable}</td><td>{c.value} {c.unit}</td><td className="muted">-{c.hours_before_image} h</td></tr>))}</tbody></table></details>}
          {task.other_annotations && <details open><summary>Independent readings</summary>
            <ul>{task.other_annotations.map((a: any, i: number) => <li key={i}>{a.reader_role}: {a.label} (confidence {a.confidence ?? "-"}) {a.comment}</li>)}</ul></details>}
          <h3>Category</h3>
          {LABELS.map(([v, l]) => (
            <label key={v} style={{ display: "block" }}><input type="radio" name="label" checked={label === v} onChange={() => setLabel(v)} /> {l}</label>))}
          <h3>Confidence</h3>
          <input type="range" min={1} max={5} value={confidence} onChange={(e) => setConfidence(Number(e.target.value))} /> {confidence}/5
          <label style={{ display: "block", marginTop: ".5rem" }}><input type="checkbox" checked={quality} onChange={(e) => setQuality(e.target.checked)} /> Image quality adequate for reading</label>
          <h3>Comment</h3>
          <textarea rows={3} style={{ width: "100%" }} value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="row" style={{ marginTop: ".5rem" }}>
            <button className="secondary" onClick={() => save(false)}>Save draft</button>
            <button onClick={() => save(true)} disabled={!label}>{submitted ? "Submit revision (new version)" : "Submit"}</button>
          </div>
          {msg && <p>{msg}</p>}
        </div>
      </div>
    </>
  );
}
