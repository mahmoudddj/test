#!/usr/bin/env bash
# Nightly backup: encrypted PostgreSQL dump + object-store mirror. Run from cron on the host.
# Requires: pg_dump, mc (MinIO client) or aws cli, gpg with the study backup public key imported.
# Restore drills must be performed and documented at least quarterly (docs/operations.md).
set -euo pipefail
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST=${BACKUP_DIR:?BACKUP_DIR required}
RECIPIENT=${BACKUP_GPG_RECIPIENT:?BACKUP_GPG_RECIPIENT required}
mkdir -p "$DEST/db" "$DEST/objects"
pg_dump --format=custom --no-owner "${PG_URL:?PG_URL required}" \
  | gpg --encrypt --recipient "$RECIPIENT" --output "$DEST/db/irtcri_$STAMP.dump.gpg"
sha256sum "$DEST/db/irtcri_$STAMP.dump.gpg" > "$DEST/db/irtcri_$STAMP.dump.gpg.sha256"
# objects: incremental mirror (raw bucket is WORM; derived can be regenerated but is mirrored for speed)
mc mirror --preserve --overwrite=false local/irtcri-raw "$DEST/objects/raw"
mc mirror --preserve local/irtcri-derived "$DEST/objects/derived"
# retention of database dumps (keep 35 daily); raw images follow the study retention policy
find "$DEST/db" -name 'irtcri_*.dump.gpg*' -mtime +35 -delete
echo "backup $STAMP complete"
