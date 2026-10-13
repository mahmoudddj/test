"""Copy existing hub images into XNAT.

    python -m scripts.migrate_images_to_xnat --dry-run          # ALWAYS first: shows the plan
    python -m scripts.migrate_images_to_xnat --report mig.json  # performs the copy + checksum verification

Source objects are never deleted. Requires XNAT_ENABLED=true and XNAT_* credentials in the environment.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="only list what would be migrated")
    p.add_argument("--limit", type=int)
    p.add_argument("--report", help="write the JSON report here")
    a = p.parse_args(argv)
    from irtcri.core.security import SYSTEM_PRINCIPAL
    from irtcri.db.session import session_factory
    from irtcri.integrations.xnat.sync import migrate_images_to_xnat
    s = session_factory()()
    rep = migrate_images_to_xnat(s, SYSTEM_PRINCIPAL, dry_run=a.dry_run, limit=a.limit)
    text = json.dumps(rep, indent=2, default=str)
    if a.report:
        open(a.report, "w").write(text)
    summary = {k: rep[k] for k in ("dry_run", "migrated", "verified")} | {"planned": len(rep["planned"]),
                                                                           "failed": len(rep["failed"])}
    print(json.dumps(summary, indent=2))
    return 1 if rep["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
