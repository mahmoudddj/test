"use client";
import { useState } from "react";
import { api, setToken, type Me } from "@/lib/api";
import Disclaimer from "@/components/Disclaimer";

export default function Login() {
  const [token, setTok] = useState("");
  const [me, setMe] = useState<Me | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function signIn() {
    setErr(null);
    setToken(token.trim());
    try { setMe(await api<Me>("/auth/me")); location.reload(); }
    catch (e) { setToken(null); setErr((e as Error).message); }
  }
  return (
    <div className="panel" style={{ maxWidth: 640 }}>
      <h1>IRT-in-CRI Study research platform</h1>
      <Disclaimer />
      <p>Sign in with your personal API token (issued by the study administrator). Tokens are kept only for this browser tab.
        Production deployments should place this UI behind the institution&apos;s single sign-on.</p>
      <div className="row">
        <input type="password" value={token} onChange={(e) => setTok(e.target.value)} placeholder="irt_..." style={{ flex: 1, minWidth: 240 }} />
        <button onClick={signIn} disabled={!token}>Sign in</button>
      </div>
      {err && <p className="error">{err}</p>}
      {me && <p>Signed in as {me.username} ({me.role}).</p>}
    </div>
  );
}
