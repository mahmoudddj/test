"use client";
import { useEffect, useRef, useState } from "react";
import { apiBlobUrl } from "@/lib/api";

export type Pt = [number, number];
export type Shapes = { insertion_point: { x: number; y: number } | null; roi: Pt[]; reference_roi: Pt[] };
type Tool = "point" | "roi" | "reference";

/** Canvas for marking the insertion site and drawing polygon ROIs, in THERMAL PIXEL coordinates. */
export default function AnnotationCanvas({ imagePath, width, height, value, onChange, readOnly }: {
  imagePath: string; width: number; height: number; value: Shapes; onChange: (s: Shapes) => void; readOnly?: boolean;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  const [tool, setTool] = useState<Tool>("point");
  const scale = Math.max(1, Math.floor(640 / Math.max(width, 1)));
  useEffect(() => {
    let url: string | null = null;
    apiBlobUrl(imagePath).then(({ url: u }) => { url = u; const i = new Image(); i.onload = () => setImg(i); i.src = u; });
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [imagePath]);
  useEffect(() => {
    const c = ref.current; if (!c) return;
    const ctx = c.getContext("2d")!;
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, c.width, c.height);
    if (img) ctx.drawImage(img, 0, 0, c.width, c.height);
    const poly = (pts: Pt[], color: string) => {
      if (!pts.length) return;
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
      pts.forEach(([x, y], i) => (i ? ctx.lineTo(x * scale, y * scale) : ctx.moveTo(x * scale, y * scale)));
      if (pts.length > 2) ctx.closePath();
      ctx.stroke();
      pts.forEach(([x, y]) => { ctx.fillStyle = color; ctx.fillRect(x * scale - 2, y * scale - 2, 4, 4); });
    };
    poly(value.roi, "#00e5ff");
    poly(value.reference_roi, "#a3e635");
    if (value.insertion_point) {
      const { x, y } = value.insertion_point;
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x * scale, y * scale, 8, 0, Math.PI * 2); ctx.moveTo(x * scale - 12, y * scale); ctx.lineTo(x * scale + 12, y * scale);
      ctx.moveTo(x * scale, y * scale - 12); ctx.lineTo(x * scale, y * scale + 12); ctx.stroke();
    }
  }, [img, value, scale]);
  function click(e: React.MouseEvent<HTMLCanvasElement>) {
    if (readOnly) return;
    const r = e.currentTarget.getBoundingClientRect();
    const x = Math.round(((e.clientX - r.left) / r.width) * width);
    const y = Math.round(((e.clientY - r.top) / r.height) * height);
    if (tool === "point") onChange({ ...value, insertion_point: { x, y } });
    if (tool === "roi") onChange({ ...value, roi: [...value.roi, [x, y]] });
    if (tool === "reference") onChange({ ...value, reference_roi: [...value.reference_roi, [x, y]] });
  }
  return (
    <div>
      {!readOnly && <div className="row" style={{ marginBottom: ".5rem" }}>
        {(["point", "roi", "reference"] as Tool[]).map((t) => (
          <button key={t} className={tool === t ? "" : "secondary"} onClick={() => setTool(t)}>
            {t === "point" ? "Insertion site" : t === "roi" ? "ROI polygon" : "Reference skin polygon"}</button>))}
        <button className="secondary" onClick={() => onChange({ ...value, [tool === "reference" ? "reference_roi" : "roi"]: [] })}>Clear polygon</button>
      </div>}
      <canvas ref={ref} width={width * scale} height={height * scale} onClick={click}
        style={{ width: "100%", maxWidth: width * scale, cursor: readOnly ? "default" : "crosshair", border: "1px solid var(--border)" }} />
    </div>
  );
}
