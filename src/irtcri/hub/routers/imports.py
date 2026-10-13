from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from irtcri.core.security import Perm, Principal
from irtcri.hub.deps import get_db, require
from irtcri.hub.importers import IMPORT_KINDS, import_file

router = APIRouter(tags=["bulk import"])


@router.get("/imports/kinds")
def kinds(_: Principal = Depends(require(Perm.WRITE_CLINICAL))):
    return {k: sorted(m.model_fields) for k, (m, _) in IMPORT_KINDS.items()}


@router.post("/imports/{kind}")
async def run_import(kind: str, file: UploadFile = File(...), site_code: str | None = Form(None),
                     timezone: str | None = Form(None), dry_run: bool = Form(True),
                     db: Session = Depends(get_db), who: Principal = Depends(require(Perm.WRITE_CLINICAL))):
    """CSV/XLSX import. Defaults to a DRY RUN: set ``dry_run=false`` to write after reviewing issues."""
    data = await file.read()
    rep = import_file(db, who, kind, data, file.filename or "upload.csv", site_code, timezone, dry_run)
    b = rep.batch
    return {"batch_id": b.id, "dry_run": b.dry_run, "sha256": b.sha256, "timezone_assumed": b.timezone_assumed,
            "rows_total": b.rows_total, "rows_accepted": b.rows_accepted, "rows_flagged": b.rows_flagged,
            "rows_rejected": b.rows_rejected, "issues": rep.issues}
