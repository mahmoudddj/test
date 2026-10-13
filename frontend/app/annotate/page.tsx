"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Item = { thermal_image_id: string; task_code: string; reader_role: string; blinding_profile: string; status: string };

export default function Worklist() {
  const [items, setItems] = useState<Item[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Item[]>("/annotation/worklist").then(setItems).catch((e) => setErr(e.message)); }, []);
  const done = items.filter((i) => i.status === "submitted").length;
  return (
    <div className="panel">
      <h1>My annotation worklist</h1>
      <p className="muted">Independent, blinded reading. You only see the information released by your blinding profile.
        Model outputs are never shown during reference labelling.</p>
      {err && <p className="error">{err}</p>}
      <p>{done}/{items.length} submitted</p>
      <table><thead><tr><th>task</th><th>role</th><th>blinding</th><th>status</th></tr></thead><tbody>
        {items.map((i) => (
          <tr key={i.thermal_image_id}><td><Link href={`/annotate/${i.thermal_image_id}`}>{i.task_code}</Link></td>
            <td>{i.reader_role}</td><td>{i.blinding_profile}</td><td>{i.status}</td></tr>))}
      </tbody></table>
    </div>
  );
}
