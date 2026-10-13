"""QC worker: runs the existing QC engine on stored images and publishes a provenance-complete QC
document (+ visualisation) to the imaging repository (XNAT QC resource when enabled).

    image (object store / XNAT) -> irtcri QC engine -> PASS/WARNING/FAIL -> qc.json + qc.png -> repository

Runs as a CLI job / Docker worker (``irtcri qc-worker``). XNAT Container Service integration can wrap
the same entry point later.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri import __version__
from irtcri.core.provenance import git_commit, sha256_bytes, sha256_json
from irtcri.core.security import Principal
from irtcri.core.timeutils import utcnow
from irtcri.db.models import RGBImage, ThermalImage
from irtcri.imaging.ingest import load_rgb, load_temperature, record_external, rerun_qc
from irtcri.imaging.qc import QCResult, load_qc_config
from irtcri.imaging.render import palette_png, thermal_png
from irtcri.imaging.repository import ImagingService, get_imaging_service, image_ref

COLORS = {"PASS": (21, 128, 61), "WARNING": (180, 83, 9), "FAIL": (185, 28, 28)}


def qc_document(row, modality: str, qc: QCResult, cfg: dict) -> dict:
    body = {"schema": "irtcri.qc/1.0", "image_id": row.id, "modality": modality,
            "acquisition_id": row.acquisition_id, "input_checksum": row.raw_sha256,
            "algorithm": "irtcri.imaging.qc.run_qc", "algorithm_version": cfg["version"],
            "software_version": __version__, "git_commit": git_commit(), "timestamp": utcnow().isoformat(),
            "thresholds": cfg, "status": qc.status, "findings": qc.reasons, "metrics": qc.metrics,
            "human_override": {"status": row.qc_override_status, "reason": row.qc_override_reason}
            if row.qc_override_status else None}
    body["output_checksum"] = sha256_json(body)
    return body


def qc_visualisation(row, modality: str, status: str, findings: list[dict]) -> bytes | None:
    try:
        if modality == "thermal":
            dec = load_temperature(row)
            png = thermal_png(dec.temperature_c)[0] if dec.temperature_c is not None else palette_png(dec.pixels)
        else:
            png = palette_png(load_rgb(row).pixels, 640)
    except Exception:
        return None
    img = Image.open(io.BytesIO(png)).convert("RGB")
    scale = max(1, 480 // max(img.size))
    img = img.resize((img.size[0] * scale, img.size[1] * scale), Image.Resampling.NEAREST)
    canvas = Image.new("RGB", (img.size[0], img.size[1] + 16 + 12 * len(findings[:6])), (255, 255, 255))
    canvas.paste(img, (0, 0))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], outline=COLORS[status], width=4)
    d.text((4, img.size[1] + 2), f"QC {status}", fill=COLORS[status])
    for i, f in enumerate(findings[:6]):
        d.text((4, img.size[1] + 14 + 12 * i), f"{f['severity']}: {f['check']}", fill=(60, 60, 60))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def run_qc_worker(session: Session, who: Principal, only_unpublished: bool = True, limit: int | None = None,
                  service: ImagingService | None = None) -> dict:
    service = service or get_imaging_service()
    cfg = load_qc_config()
    summary = {"processed": 0, "skipped_up_to_date": 0, "published_xnat": 0, "errors": 0,
               "status": {"PASS": 0, "WARNING": 0, "FAIL": 0}}
    for Model, modality in ((ThermalImage, "thermal"), (RGBImage, "rgb")):
        q = select(Model).where(Model.is_voided.is_(False)).order_by(Model.acquisition_time)
        if service.xnat_enabled:
            q = q.where(Model.xnat_uri.is_not(None))
        for row in session.scalars(q):
            if limit is not None and summary["processed"] >= limit:
                return summary
            ref = image_ref(session, row, modality)
            repo = service.xnat if service.xnat_enabled else service.local
            if only_unpublished:
                prev = repo.get_qc_result(ref)
                if prev and prev.get("algorithm_version") == cfg["version"] and \
                        prev.get("input_checksum") == row.raw_sha256:
                    summary["skipped_up_to_date"] += 1
                    continue
            try:
                qc = rerun_qc(session, who, modality, row.id, cfg)
                doc = qc_document(row, modality, qc, cfg)
                png = qc_visualisation(row, modality, qc.status, qc.reasons)
                loc = service.store_qc_result(ref, doc, png)
                if loc["xnat"]:
                    record_external(session, "xnat", Model.__tablename__, row.id, "qc", loc["xnat"],
                                    sha256_bytes(str(doc["output_checksum"]).encode()))
                    summary["published_xnat"] += 1
                summary["processed"] += 1
                summary["status"][qc.status] += 1
                session.commit()
            except Exception:
                session.rollback()
                summary["errors"] += 1
    return summary
