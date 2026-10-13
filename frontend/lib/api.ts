// Thin API client. The bearer token is kept in sessionStorage (cleared when the tab closes).
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const KEY = "irtcri_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try { return window.sessionStorage.getItem(KEY); } catch { return null; }
}
export function setToken(t: string | null) {
  try {
    if (t) window.sessionStorage.setItem(KEY, t); else window.sessionStorage.removeItem(KEY);
  } catch { /* storage unavailable */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const t = getToken();
  if (t) headers.set("Authorization", `Bearer ${t}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const r = await fetch(`${API_BASE}/api/v1${path}`, { ...init, headers, cache: "no-store" });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch { /* */ }
    throw new ApiError(r.status, msg);
  }
  const ct = r.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? r.json() : r.text()) as Promise<T>;
}

export async function apiBlobUrl(path: string): Promise<{ url: string; headers: Headers }> {
  const t = getToken();
  const r = await fetch(`${API_BASE}/api/v1${path}`, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
  if (!r.ok) throw new ApiError(r.status, r.statusText);
  return { url: URL.createObjectURL(await r.blob()), headers: r.headers };
}

export type Me = { username: string; role: string; site_id: string | null; permissions: string[] };
export const DISCLAIMER = "This system is investigational and is not a replacement for clinical judgement.";
