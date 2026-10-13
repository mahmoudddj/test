"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, getToken, setToken, type Me } from "@/lib/api";

export default function Nav() {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    if (!getToken()) return;
    api<Me>("/auth/me").then(setMe).catch(() => setMe(null));
  }, []);
  const can = (p: string) => me?.permissions.includes(p);
  return (
    <header className="nav">
      <span className="brand">IRT-CRI</span>
      {can("view_dq") && <Link href="/dq">Data quality</Link>}
      {can("read_clinical") && <Link href="/patients">Patients / timeline</Link>}
      {can("annotate") && <Link href="/annotate">Annotation</Link>}
      {can("adjudicate") && <Link href="/adjudicate">Adjudication</Link>}
      <span style={{ flex: 1 }} />
      {me ? (
        <span className="muted">
          {me.username} ({me.role}){" "}
          <button className="secondary" onClick={() => { setToken(null); location.href = "/"; }}>Sign out</button>
        </span>
      ) : <Link href="/">Sign in</Link>}
    </header>
  );
}
