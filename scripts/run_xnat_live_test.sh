#!/usr/bin/env bash
# Run the live XNAT validation against a DISPOSABLE TEST PROJECT (never the production project).
#   export XNAT_TEST_URL=https://xnat-test.example.org XNAT_TEST_USERNAME=irtcri-test \
#          XNAT_TEST_PASSWORD='...' XNAT_TEST_PROJECT=IRTCRI_TEST XNAT_TEST_CONFIRM=test-project-only
#   scripts/run_xnat_live_test.sh
set -u
missing=0
for v in XNAT_TEST_URL XNAT_TEST_USERNAME XNAT_TEST_PASSWORD XNAT_TEST_PROJECT; do
  if [ -z "${!v:-}" ] && ! { [ "$v" = XNAT_TEST_USERNAME ] && [ -n "${XNAT_TEST_USER:-}" ]; }; then
    echo "missing: $v"; missing=1; fi
done
[ "${XNAT_TEST_CONFIRM:-}" = "test-project-only" ] || { echo 'missing: XNAT_TEST_CONFIRM=test-project-only'; missing=1; }
[ $missing -eq 0 ] || { echo "Not running. Set the variables above (see tests/integration/test_xnat_live.py)."; exit 2; }
exec python -m pytest tests/integration/test_xnat_live.py -v -m xnat_live -p no:cacheprovider
