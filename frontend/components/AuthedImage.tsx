"use client";
import { useEffect, useState } from "react";
import { apiBlobUrl } from "@/lib/api";

/** <img> for endpoints that need the bearer token. */
export default function AuthedImage({ path, alt, style, onWindow }: {
  path: string; alt: string; style?: React.CSSProperties; onWindow?: (w: [number, number] | null) => void;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let url: string | null = null;
    setErr(null);
    apiBlobUrl(path).then(({ url: u, headers }) => {
      url = u; setSrc(u);
      const w = headers.get("X-Temperature-Window");
      onWindow?.(w ? (w.split(",").map(Number) as [number, number]) : null);
    }).catch((e) => setErr(String(e.message ?? e)));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [path]); // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <div className="error">Image unavailable: {err}</div>;
  if (!src) return <div className="muted">loading image...</div>;
  return <img src={src} alt={alt} style={{ maxWidth: "100%", imageRendering: "pixelated", ...style }} />;
}
