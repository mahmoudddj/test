"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useRef, useState } from "react";
import AuthedImage from "@/components/AuthedImage";
import { api, DISCLAIMER } from "@/lib/api";

export default function ThermalViewer({ image, prediction }: { image: any; prediction: any | null }) {
  const [overlay, setOverlay] = useState(false);
  const [grid, setGrid] = useState<any | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [win, setWin] = useState<[number, number] | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { setGrid(null); api(`/images/thermal/${image.id}/temperature?max_side=160`).then(setGrid).catch(() => setGrid(null)); }, [image.id]);
  const path = `/images/thermal/${image.id}/render.png${overlay && prediction?.has_explanation ? `?prediction_id=${prediction.id}` : ""}`;
  function onMove(e: React.MouseEvent) {
    if (!grid || !box.current) return;
    const img = box.current.querySelector("img");
    if (!img) return;
    const r = img.getBoundingClientRect();
    const gx = Math.floor(((e.clientX - r.left) / r.width) * grid.width);
    const gy = Math.floor(((e.clientY - r.top) / r.height) * grid.height);
    const v = grid.values[gy]?.[gx];
    setHover(v == null ? null : `${v.toFixed(2)} degC`);
  }
  return (
    <div className="panel">
      <h3>Image {image.time} <span className={image.qc_status}>QC {image.qc_status}</span></h3>
      <div className="row">
        <div style={{ flex: 1, minWidth: 260 }} ref={box} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          <AuthedImage path={path} alt="thermal image" style={{ width: "100%" }} onWindow={setWin} />
          <div className="muted">{hover ? `cursor: ${hover}` : grid ? "hover for temperature" : "temperature map unavailable"}
            {win && ` | colour window ${win[0].toFixed(1)}-${win[1].toFixed(1)} degC`}</div>
          {prediction?.has_explanation && <label><input type="checkbox" checked={overlay} onChange={(e) => setOverlay(e.target.checked)} /> Grad-CAM overlay (research aid; not evidence of causal reasoning)</label>}
        </div>
        {image.rgb_image_id && <div style={{ flex: 1, minWidth: 260 }}>
          <AuthedImage path={`/images/rgb/${image.rgb_image_id}/render.png`} alt="RGB image" style={{ width: "100%", imageRendering: "auto" }} />
          <div className="muted">paired RGB</div></div>}
        <div style={{ flex: 1, minWidth: 240 }}>
          {image.reference_label && <p>Reference label (adjudicated): <strong>{image.reference_label}</strong></p>}
          {prediction ? (
            <div className={prediction.elevated_risk ? "alert" : "panel"}>
              <strong>{prediction.message}</strong>
              <div>Model-estimated probability: {prediction.probability.toFixed(3)} (research threshold {prediction.threshold.toFixed(3)})</div>
              <div className="muted">Model {prediction.model_version} [{prediction.model_status}], mode: {prediction.mode}</div>
              {prediction.feature_contributions?.contributions && <>
                <div style={{ marginTop: ".5rem" }}>Largest clinical feature contributions ({prediction.feature_contributions.method}, {prediction.feature_contributions.unit}):</div>
                <table><tbody>{Object.entries(prediction.feature_contributions.contributions).slice(0, 8).map(([k, v]) => (
                  <tr key={k}><td>{k}</td><td>{(v as number) > 0 ? "+" : ""}{(v as number).toFixed(3)}</td></tr>))}</tbody></table></>}
              <p className="muted" style={{ fontSize: ".8rem" }}>{DISCLAIMER}</p>
            </div>
          ) : <p className="muted">No model output stored for this image.</p>}
        </div>
      </div>
    </div>
  );
}
