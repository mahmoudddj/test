#!/usr/bin/env bash
# End-to-end demonstration on SYNTHETIC data (no real patient data involved).
# Usage: scripts/run_demo.sh [workdir] [n_patients]
set -euo pipefail
WORK=${1:-var/demo}
N=${2:-20}
mkdir -p "$WORK"
export IRTCRI_DATABASE_URL="sqlite:///$WORK/hub.db"
export IRTCRI_LOCAL_STORE_ROOT="$WORK/objects"
export IRTCRI_DATA_DIR="$WORK/data"
DS="$WORK/data/datasets/irtcri_early_warning/0.1.0"
RUNS="$WORK/runs"

irtcri init-db
irtcri synthetic --n-patients "$N" --seed 11
irtcri build-dataset configs/datasets/primary_early_warning.yaml
cat "$DS/ml/cohort_flow.md"

for cfg in f0_clinical_logreg f0_clinical_thermal_summary_logreg smoke_f4_simplecnn; do
  irtcri train "configs/ml/$cfg.yaml" --set dataset_root="$DS" output_root="$RUNS"
done

BEST=$(ls -d "$RUNS"/f0b_clinical_thermalsummary_logreg/* | tail -1)
irtcri evaluate-val "$BEST" --n-boot 300
# One-time locked test evaluation of the pre-specified model (demonstration only):
irtcri evaluate-test "$BEST" --confirm-final --n-boot 300

CNN=$(ls -d "$RUNS"/smoke_f4_simplecnn/* | tail -1)
irtcri replay "$CNN" --splits val --limit 200
irtcri study-analysis "$DS" --out "$WORK/reports/study"
irtcri xnat-check 2>/dev/null || echo "XNAT disabled (optional)"
irtcri site-report S01 2026-02 --out "$WORK/reports/sites"
echo "Demo complete. Start the API with: IRTCRI_DATABASE_URL=$IRTCRI_DATABASE_URL IRTCRI_LOCAL_STORE_ROOT=$IRTCRI_LOCAL_STORE_ROOT irtcri serve"
