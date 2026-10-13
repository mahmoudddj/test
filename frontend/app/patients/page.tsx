"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type P = { study_id: string; enrolment_date: string; sex: string; age_years: number | null; withdrawn: boolean };

export default function Patients() {
  const [rows, setRows] = useState<P[]>([]);
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<P[]>("/patients").then(setRows).catch((e) => setErr(e.message)); }, []);
  const shown = rows.filter((r) => r.study_id.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="panel">
      <h1>Participants</h1>
      {err && <p className="error">{err}</p>}
      <input placeholder="filter study ID" value={q} onChange={(e) => setQ(e.target.value)} />
      <table><thead><tr><th>study ID</th><th>enrolled</th><th>sex</th><th>age</th><th></th></tr></thead><tbody>
        {shown.map((r) => (
          <tr key={r.study_id}><td><Link href={`/patients/${r.study_id}`}>{r.study_id}</Link></td><td>{r.enrolment_date}</td>
            <td>{r.sex}</td><td>{r.age_years ?? "-"}</td><td>{r.withdrawn ? "withdrawn" : ""}</td></tr>))}
      </tbody></table>
    </div>
  );
}
