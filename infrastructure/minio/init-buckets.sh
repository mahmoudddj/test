#!/bin/sh
# Create buckets with versioning; RAW bucket gets Object Lock (WORM) in compliance mode so that
# original images cannot be overwritten or deleted during the retention period.
set -eu
mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb --ignore-existing --with-lock local/irtcri-raw
mc mb --ignore-existing local/irtcri-derived
mc version enable local/irtcri-derived
mc retention set --default COMPLIANCE "${RAW_RETENTION_DAYS}d" local/irtcri-raw || true
mc encrypt set sse-s3 local/irtcri-raw || echo "WARN: enable KMS for SSE in production"
mc encrypt set sse-s3 local/irtcri-derived || true
# least-privilege application user: read/write objects, cannot change bucket policy/retention
cat > /tmp/app-policy.json <<POL
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:ListBucket","s3:GetObjectVersion"],
  "Resource":["arn:aws:s3:::irtcri-raw","arn:aws:s3:::irtcri-raw/*","arn:aws:s3:::irtcri-derived","arn:aws:s3:::irtcri-derived/*"]}]}
POL
mc admin policy create local irtcri-app /tmp/app-policy.json || true
mc admin user add local "$APP_S3_ACCESS_KEY" "$APP_S3_SECRET_KEY" || true
mc admin policy attach local irtcri-app --user "$APP_S3_ACCESS_KEY" || true
echo "buckets ready"
