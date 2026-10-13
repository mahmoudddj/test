from __future__ import annotations

import io
import json
import zipfile

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from irtcri.core.provenance import code_version, sha256_bytes
from irtcri.core.security import Perm, Principal
from irtcri.core.timeutils import utcnow
from irtcri.db.audit import audit
from irtcri.hub.deps import get_db, require
from irtcri.pipeline.snapshot import exclude_withdrawn_without_consent, load_tables
from irtcri.schemas.dictionary import dictionary_markdown, load_dictionary

router = APIRouter(tags=["exports"])


@router.post("/exports/snapshot", summary="Export a pseudonymised hub snapshot (parquet + CSV + manifest)")
def snapshot(include_voided: bool = False, db: Session = Depends(get_db),
             who: Principal = Depends(require(Perm.EXPORT_DATA))):
    site_ids = None if who.can(Perm.ALL_SITES) else [who.site_id]
    tables = load_tables(db, include_voided=include_voided, site_ids=site_ids)
    tables, n_excluded = exclude_withdrawn_without_consent(tables)
    buf = io.BytesIO()
    manifest = {"created_time": utcnow().isoformat(), "exported_by": who.username, "code": code_version(),
                "data_dictionary_version": load_dictionary().version, "include_voided": include_voided,
                "participants_excluded_withdrawn_no_data_use": n_excluded,
                "files": {}}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, df in tables.items():
            pq = io.BytesIO()
            df.to_parquet(pq, index=False)
            csv = df.to_csv(index=False).encode()
            z.writestr(f"parquet/{name}.parquet", pq.getvalue())
            z.writestr(f"csv/{name}.csv", csv)
            manifest["files"][name] = {"rows": int(len(df)), "sha256_csv": sha256_bytes(csv)}
        z.writestr("data_dictionary.md", dictionary_markdown())
        z.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
    rows = {k: v["rows"] for k, v in manifest["files"].items()}
    audit(db, who, "export", "hub_snapshot", None, after={"tables": rows})
    buf.seek(0)
    fname = f"irtcri_snapshot_{utcnow():%Y%m%dT%H%M%SZ}.zip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})
