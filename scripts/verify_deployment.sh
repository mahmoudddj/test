#!/usr/bin/env bash
# Post-deployment check for a RUNNING IRT-CRI platform. Read-only except one export (audited).
#
#   IRTCRI_TOKEN=<admin or data-manager token> scripts/verify_deployment.sh
#
# Optional settings (defaults in brackets):
#   API_URL [http://localhost:8000]   FRONTEND_URL [http://localhost:3000]
#   MLFLOW_URL [unset -> SKIPPED]     XNAT is checked through the API when XNAT_ENABLED=true there.
set -u
API_URL=${API_URL:-http://localhost:8000}
FRONTEND_URL=${FRONTEND_URL:-http://localhost:3000}
MLFLOW_URL=${MLFLOW_URL:-}
TOKEN=${IRTCRI_TOKEN:-}
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
FAILED=0
row() { printf "%-17s %s\n" "$1" "$2"; [ "$2" = "FAIL" ] || [[ "$2" == FAIL* ]] && FAILED=1; return 0; }
json() { python3 -c "import json,sys; d=json.load(open('$1')); print($2)" 2>/dev/null; }
api() { curl -sS -m 60 -H "Authorization: Bearer $TOKEN" -o "$TMP/$1" -w "%{http_code}" "${@:2}"; }

# ---------------------------------------------------------------- infrastructure
code=$(curl -sS -m 20 -o "$TMP/ready.json" -w "%{http_code}" "$API_URL/health/ready" 2>/dev/null || echo 000)
if [ "$code" = "000" ]; then
  row "PostgreSQL" "FAIL (API unreachable)"; row "Backend" "FAIL"; row "Object storage" "FAIL (API unreachable)"
else
  [ "$(json "$TMP/ready.json" "d['checks']['database']")" = "ok" ] && row "PostgreSQL" "PASS" || row "PostgreSQL" "FAIL"
  h=$(curl -sS -m 20 "$API_URL/health" 2>/dev/null)
  [[ "$h" == *'"status":"ok"'* ]] && row "Backend" "PASS" || row "Backend" "FAIL"
  [ "$(json "$TMP/ready.json" "d['checks']['object_store']")" = "ok" ] && row "Object storage" "PASS" || row "Object storage" "FAIL"
fi
fc=$(curl -sS -m 20 -o /dev/null -w "%{http_code}" "$FRONTEND_URL/" 2>/dev/null || echo 000)
[ "$fc" = "200" ] && row "Frontend" "PASS" || row "Frontend" "FAIL (HTTP $fc)"
if [ -n "$MLFLOW_URL" ]; then
  mc=$(curl -sS -m 20 -o /dev/null -w "%{http_code}" "$MLFLOW_URL/health" 2>/dev/null || echo 000)
  [ "$mc" = "200" ] && row "MLflow" "PASS" || row "MLflow" "FAIL (HTTP $mc)"
else
  row "MLflow" "SKIPPED"
fi
x=$(json "$TMP/ready.json" "d['checks']['xnat']")
case "$x" in ok) row "XNAT" "PASS";; disabled|"") row "XNAT" "SKIPPED";; *) row "XNAT" "FAIL ($x)";; esac

# ---------------------------------------------------------------- functional checks
if [ -z "$TOKEN" ]; then
  for c in "Demo data" "Catheter episode" "Thermal image" "QC" "Dataset export"; do row "$c" "SKIPPED (set IRTCRI_TOKEN)"; done
  exit $FAILED
fi
sid=""; [ "$(api patients.json "$API_URL/api/v1/patients")" = "200" ] && sid=$(json "$TMP/patients.json" "d[0]['study_id']")
[ -n "$sid" ] && row "Demo data" "PASS" || row "Demo data" "FAIL"
ep=""; [ -n "$sid" ] && [ "$(api eps.json "$API_URL/api/v1/episodes?study_id=$sid")" = "200" ] && ep=$(json "$TMP/eps.json" "d[0]['episode_code']")
[ -n "$ep" ] && row "Catheter episode" "PASS" || row "Catheter episode" "FAIL"
img=""; [ -n "$ep" ] && [ "$(api imgs.json "$API_URL/api/v1/images/thermal?episode_code=$ep&qc_status=PASS&limit=5")" = "200" ] \
  && img=$(json "$TMP/imgs.json" "d[0]['id']")
if [ -n "$img" ] && [ "$(api img.png "$API_URL/api/v1/images/thermal/$img/render.png")" = "200" ] \
   && [ "$(head -c 4 "$TMP/img.png" | tail -c 3)" = "PNG" ]; then row "Thermal image" "PASS"; else row "Thermal image" "FAIL"; fi
q=$(json "$TMP/imgs.json" "d[0]['qc_status'] + ' ' + str(d[0]['qc_config_version'])")
[[ "$q" =~ ^(PASS|WARNING|FAIL)\ qc- ]] && row "QC" "PASS" || row "QC" "FAIL"
if [ "$(api export.zip -X POST "$API_URL/api/v1/exports/snapshot")" = "200" ] && python3 - "$TMP/export.zip" <<'PY'
import json, sys, zipfile
z = zipfile.ZipFile(sys.argv[1]); names = z.namelist()
m = json.loads(z.read("manifest.json"))
sys.exit(0 if "parquet/patient.parquet" in names and m["files"]["patient"]["rows"] > 0 else 1)
PY
then row "Dataset export" "PASS"; else row "Dataset export" "FAIL"; fi
exit $FAILED
