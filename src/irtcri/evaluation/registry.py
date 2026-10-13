"""Manuscript analysis registry.

``register_analysis`` freezes, for one manuscript result set:
paper_id, analysis_version, dataset (name/version/checksum/lock state), git commit, the statistical
configuration (stored in full + hash), model version (+ model-card hash), software environment and
checksums of every output file. Records are append-only: a changed analysis needs a new
analysis_version. A JSON copy is written next to the outputs for the manuscript archive.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from irtcri.core.provenance import code_version, sha256_file, sha256_json
from irtcri.core.security import Perm, Principal
from irtcri.db.audit import audit, row_snapshot
from irtcri.db.models import AnalysisRecord, DatasetVersion
from irtcri.hub.services import Conflict, Forbidden, NotFound


def register_analysis(session: Session, who: Principal, paper_id: str, analysis_version: str,
                      dataset_root: str | Path, stat_config: str | Path | dict, outputs_dir: str | Path | None = None,
                      model_run_dir: str | Path | None = None, description: str | None = None,
                      allow_unlocked: bool = False) -> AnalysisRecord:
    if not (who.can(Perm.EXPORT_DATA) or who.can(Perm.MANAGE_MODELS)):
        raise Forbidden("Statistician / MLResearcher / DataManager role required")
    if session.scalar(select(AnalysisRecord).where(AnalysisRecord.paper_id == paper_id,
                                                   AnalysisRecord.analysis_version == analysis_version)):
        raise Conflict(f"{paper_id} v{analysis_version} already registered (records are immutable)")
    manifest = json.loads((Path(dataset_root) / "manifest.json").read_text())
    dv = session.scalar(select(DatasetVersion).where(DatasetVersion.name == manifest["name"],
                                                     DatasetVersion.version == manifest["version"]))
    if dv is None:
        raise NotFound("dataset version not registered in the hub")
    if not dv.locked and not allow_unlocked:
        raise Conflict("manuscript analyses must use a LOCKED dataset (irtcri lock-dataset); "
                       "pass allow_unlocked only for drafts")
    cfg = stat_config if isinstance(stat_config, dict) else yaml.safe_load(Path(stat_config).read_text())
    model_version = card_sha = None
    if model_run_dir:
        card_path = Path(model_run_dir) / "model_card.json"
        card = json.loads(card_path.read_text())
        model_version = f"{card['model_name']}@{card['model_version']}"
        card_sha = sha256_file(card_path)
    outputs = None
    if outputs_dir:
        od = Path(outputs_dir)
        outputs = {str(p.relative_to(od)): sha256_file(p) for p in sorted(od.rglob("*"))
                   if p.is_file() and p.name != "analysis_record.json"}
    env = code_version()
    rec = AnalysisRecord(paper_id=paper_id, analysis_version=analysis_version, description=description,
                         dataset_name=manifest["name"], dataset_version=manifest["version"],
                         dataset_checksum=manifest["checksum"], dataset_locked=dv.locked,
                         git_commit=env["git_commit"], statistical_config=cfg,
                         statistical_config_sha256=sha256_json(cfg), model_version=model_version,
                         model_card_sha256=card_sha, environment=env, outputs=outputs, created_by=who.username)
    session.add(rec)
    session.flush()
    audit(session, who, "register_analysis", "analysis_record", rec.id, after=row_snapshot(rec))
    if outputs_dir:
        (Path(outputs_dir) / "analysis_record.json").write_text(json.dumps(row_snapshot(rec), indent=2, default=str))
    return rec
