"""Watch-folder ingestion job (run by cron/systemd timer or `irtcri watch-folder`).

Files are never moved or deleted. A JSONL ledger records what was processed so
rescans are idempotent; ingestion is also idempotent at database level.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from irtcri.core.security import Principal
from irtcri.core.timeutils import utcnow
from irtcri.db.models import CatheterEpisode, Site
from irtcri.imaging.adapters import FolderExportAdapter
from irtcri.imaging.ingest import IngestError, acquisition_meta_from_sidecar, ingest_image, resolve_episode


def scan_folder(session: Session, who: Principal, folder: str | Path, ledger: str | Path | None = None,
                reader_options: dict | None = None) -> dict:
    folder = Path(folder)
    ledger_path = Path(ledger) if ledger else folder / ".irtcri_ingest_ledger.jsonl"
    seen: set[str] = set()
    if ledger_path.exists():
        for line in ledger_path.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("status") == "ingested":
                seen.add(rec["sha256"] + "|" + rec["file"])
    summary = {"ingested": 0, "skipped": 0, "errors": 0, "qc": {"PASS": 0, "WARNING": 0, "FAIL": 0}}
    with open(ledger_path, "a") as led:
        for cap in FolderExportAdapter(folder).captures():
            rec = {"time": utcnow().isoformat(), "file": cap.source_path}
            if cap.modality == "unknown":
                rec.update(status="error", error=cap.meta.get("error"), sha256=None)
                summary["errors"] += 1
                led.write(json.dumps(rec) + "\n")
                continue
            rec["sha256"] = cap.sha256
            if cap.sha256 + "|" + str(cap.source_path) in seen:
                summary["skipped"] += 1
                continue
            try:
                ep: CatheterEpisode = resolve_episode(session, cap.meta["episode_id"])
                site = session.get(Site, ep.site_id)
                meta = acquisition_meta_from_sidecar(cap.meta, site.timezone)
                meta["episode_id"] = ep.id
                with session.begin_nested():
                    res = ingest_image(session, who, cap.modality, cap.data, cap.filename, meta,
                                       channel="watch_folder", reader_options=reader_options)
                session.commit()
                rec.update(status="ingested", image_id=res.image.id, qc_status=res.image.qc_status,
                           already_present=res.already_present)
                summary["ingested"] += 1
                summary["qc"][res.image.qc_status] = summary["qc"].get(res.image.qc_status, 0) + 1
            except (IngestError, KeyError, ValueError, PermissionError) as e:
                session.rollback()
                rec.update(status="error", error=str(e))
                summary["errors"] += 1
            led.write(json.dumps(rec) + "\n")
    return summary
