"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
import AuthedImage from "@/components/AuthedImage";
import { api } from "@/lib/api";

const LABELS = ["no_infection", "possible_infection", "exit_site_infection", "tunnel_infection", "pocket_infection", "crbsi", "uncertain_insufficient_data"];

export default function Adjudicate() {
  const [queue, setQueue] = useState<any[]>([]);
  const [cur, setCur] = useState<any | null>(null);
  const [label, setLabel] = useState("");
  const [rationale, setRationale] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api<any[]>("/annotation/adjudication-queue").then(setQueue).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  async function decide() {
    try {
      await api("/annotation/adjudications", { method: "POST", body: JSON.stringify({ thermal_image_id: cur.thermal_image_id, final_label: label, rationale }) });
      setMsg("Adjudication saved."); setCur(null); setLabel(""); setRationale(""); load();
    } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <div className="row">
      <div className="panel" style={{ flex: 1, minWidth: 280 }}>
        <h1>Adjudication queue</h1>
        <p className="muted">Images where the two independent readers disagree. Agreeing reads are adjudicated automatically.
          You cannot adjudicate images you read yourself.</p>
        <table><thead><tr><th>image</th><th>reader 1</th><th>reader 2</th></tr></thead><tbody>
          {queue.map((q) => (
            <tr key={q.thermal_image_id} style={{ cursor: "pointer" }} onClick={() => setCur(q)}>
              <td>{q.thermal_image_id.slice(0, 8)}</td><td>{q.reader_1_label}</td><td>{q.reader_2_label}</td></tr>))}
        </tbody></table>
        {msg && <p>{msg}</p>}
      </div>
      {cur && <div className="panel" style={{ flex: 2, minWidth: 320 }}>
        <AuthedImage path={`/images/thermal/${cur.thermal_image_id}/render.png`} alt="thermal" style={{ width: "100%", maxWidth: 640 }} />
        <p>Reader 1: <strong>{cur.reader_1_label}</strong> | Reader 2: <strong>{cur.reader_2_label}</strong></p>
        <select value={label} onChange={(e) => setLabel(e.target.value)}><option value="">final reference label...</option>
          {LABELS.map((l) => <option key={l} value={l}>{l}</option>)}</select>
        <textarea rows={3} style={{ width: "100%", marginTop: ".5rem" }} placeholder="rationale (criteria applied)" value={rationale} onChange={(e) => setRationale(e.target.value)} />
        <button onClick={decide} disabled={!label}>Save final label</button>
      </div>}
    </div>
  );
}
