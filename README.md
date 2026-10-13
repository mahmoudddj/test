# IRT-CRI research platform

Research software for the **IRT-in-CRI Study**: AI-powered infrared thermography for early detection and
treatment monitoring of catheter-related infections, in a multicentre setting.

> **Investigational research software.** It is not a medical device, not a diagnostic system, and not a
> replacement for clinical judgement. It makes no regulatory or legal compliance claim; see
> [docs/SECURITY_GOVERNANCE.md](docs/SECURITY_GOVERNANCE.md).

## What it does

| Product | Status | Entry points |
|---|---|---|
| A Data Hub: sites, screening, Study IDs, catheter episodes, vitals/labs, medications, microbiology, assessments, outcomes, deviations, queries, corrections, withdrawal, audit, import/export | ✅ | REST `/api/v1`, `/docs`, `irtcri import` |
| B Thermal/RGB ingestion + configurable QC (PASS/WARNING/FAIL), write-once raw storage, derivation provenance, watch folder, optional **XNAT** | ✅ | upload API, `watch-folder`, `qc-worker`, `xnat-*` |
| C Blinded annotation, independent readers, adjudication, inter-rater reliability | ✅ | UI `/annotate`, `/adjudicate` |
| D Dataset builder (raw → validated → longitudinal → ML → locked), leakage-safe features, patient-level splits, cohort flow, manifests | ✅ | `build-dataset`, `lock-dataset` |
| E ML families F0–F5 (scikit-learn, ResNet/EfficientNet/ViT, fusion, U-Net), test-set locking, MLflow optional | ✅ | `train`, `evaluate-test` |
| F Evaluation and biostatistics (clustered CIs, early-warning lead time, calibration, KM/Cox/GEE/mixed) | ✅ | `evaluate-val`, `study-analysis` |
| G Interpretability (Grad-CAM, linear/SHAP/occlusion attributions) | ✅ | stored per prediction |
| H Pilot dashboard (timeline, viewer, research alert, retrospective replay) | ✅ | UI `/patients/<id>`, `replay` |
| I Data-quality and study-monitoring dashboard, monthly site reports | ✅ | UI `/dq`, `site-report` |
| Research integrity: analysis registry, lock files, migrations, manifests | ✅ | `register-analysis` |

Screenshots (SYNTHETIC data): [DQ dashboard](docs/screenshots/01_data_quality_dashboard.png) ·
[timeline + viewer](docs/screenshots/02_patient_timeline_and_viewer.png) ·
[blinded annotation](docs/screenshots/03_blinded_annotation.png) ·
[adjudication](docs/screenshots/04_adjudication.png)

## Run it — simplest way (any computer or server with Docker)

1. Install **Docker Desktop** (Windows/Mac) or **Docker Engine + Compose** (Linux) and **git**.
2. Get the code:
   `git clone https://github.com/mahmoudddj/test.git irtcri && cd irtcri`
3. Settings: for a quick look on your own computer, nothing is needed (safe local defaults are built in).
   On a shared server: `cp .env.example .env`, then replace every `change-me` value.
4. Start everything, including 20 SYNTHETIC demo patients:
   `docker compose --profile demo up -d --build`   (first run takes ~5–10 min)
5. Create the first administrator (once). Copy the `token` it prints and keep it safe:
   `curl -X POST "http://localhost:8000/api/v1/auth/bootstrap?username=admin" -H "X-Bootstrap-Token: dev-bootstrap-token"`
   (on a server, use the `IRTCRI_BOOTSTRAP_ADMIN_TOKEN` value from your `.env`)
6. Check it works: `IRTCRI_TOKEN=<your token> scripts/verify_deployment.sh`
7. Open **http://localhost:3000** and paste the token to sign in.

| What | Address |
|---|---|
| Web app (dashboards, timeline, annotation) | http://localhost:3000 |
| API documentation | http://localhost:8000/docs |
| Health / readiness | http://localhost:8000/health · http://localhost:8000/health/ready |
| MinIO console (image storage admin) | http://localhost:9001 |
| MLflow (only if started with `--profile tracking`) | http://localhost:5000 |
| XNAT | only if `XNAT_ENABLED=true`; it runs on its own server (see docs/XNAT_INTEGRATION.md) |

Create other users (data managers, site researchers, annotators...) in the API docs page with
`POST /api/v1/users` while signed in as admin. Stop: `docker compose down` (data are kept).

## Quick start for developers

### 1. Laptop: everything on SYNTHETIC data (no Docker)
```bash
pip install -e ".[dev,ml]"           # or: pip install -r requirements/lock-ml.txt && pip install --no-deps -e .
pytest                                # 72 tests (+1 optional live-XNAT test, skipped)
scripts/run_demo.sh var/demo 20       # synthetic data -> dataset -> models -> locked test -> replay -> statistics -> site report
```

### 2. Server stack with Docker (PostgreSQL + MinIO + API + web UI)
```bash
docker compose up --build             # dev defaults, bound to 127.0.0.1; use .env (see .env.example) for any shared host
docker compose --profile demo up      # additionally loads 20 SYNTHETIC patients
# first Admin (bootstrap token from .env, dev default "dev-bootstrap-token"):
curl -X POST "localhost:8000/api/v1/auth/bootstrap?username=admin" -H "X-Bootstrap-Token: dev-bootstrap-token"
# then sign in at http://localhost:3000 with the returned token; API docs at http://localhost:8000/docs
```
Behind a TLS-intercepting proxy, build with `--secret id=pip_ca,src=<ca.crt>` (API) / `--secret id=npm_ca,...` (UI).
If `minio/*` images are unavailable from your registry, set `MINIO_IMAGE` / `MC_IMAGE` to an image your
organisation mirrors.

### 3. API examples
```bash
H="Authorization: Bearer $TOKEN"
curl -H "$H" -X POST localhost:8000/api/v1/sites -H 'Content-Type: application/json' \
  -d '{"site_code":"S01","name":"Site 1","timezone":"Asia/Riyadh"}'
curl -H "$H" -X POST localhost:8000/api/v1/patients -H 'Content-Type: application/json' \
  -d '{"site_code":"S01","enrolment_date":"2026-03-01","age_years":64,"sex":"female"}'          # -> S01-0001
curl -H "$H" -X POST localhost:8000/api/v1/episodes -H 'Content-Type: application/json' \
  -d '{"study_id":"S01-0001","catheter_type":"HD_CVC","insertion_site":"internal_jugular_right","insertion_time":"2026-03-01T09:30:00+03:00"}'
curl -H "$H" -X POST localhost:8000/api/v1/measurements -H 'Content-Type: application/json' \
  -d '{"study_id":"S01-0001","episode_code":"S01-0001-C1","variable":"creatinine","value":1.4,"unit":"mg/dL","event_time":"2026-03-01T10:00:00+03:00","source_time":"2026-03-01T12:10:00+03:00"}'
curl -H "$H" -F file=@thermal.csv -F 'metadata={"episode_code":"S01-0001-C1","acquisition_time":"2026-03-01T12:00:00+03:00","distance_cm":40,"ambient_temperature_c":23,"emissivity":0.98,"device_id":"S01-CAM1","operator_id":"OP2"}' \
  localhost:8000/api/v1/images/thermal                                                         # -> {"qc_status": "PASS", ...}
curl -H "$H" -F file=@sample_data/templates/measurements_wide.csv -F site_code=S01 localhost:8000/api/v1/imports/measurements   # dry run report
curl -H "$H" localhost:8000/api/v1/dq/summary
curl -H "$H" -X POST localhost:8000/api/v1/exports/snapshot -o snapshot.zip
```

## CLI overview
`irtcri --help`: `init-db`, `create-user`, `synthetic`, `import`, `watch-folder`, `qc-worker`, `build-dataset`,
`lock-dataset`, `train`, `evaluate-val`, `evaluate-test`, `replay`, `study-analysis`, `site-report`,
`register-analysis`, `dictionary-docs`, `xnat-check`, `xnat-push`, `xnat-pull`, `xnat-publish-annotations`, `serve`.

## Repository layout
```
src/irtcri/
  core/          config (env vars), RBAC, provenance, timezone handling, PHI-safe logging
  schemas/       controlled vocabularies + data-dictionary engine
  db/            SQLAlchemy models, sessions, audit writer
  hub/           FastAPI app, routers, domain services, CSV/XLSX importers          (Product A)
  imaging/       readers, QC, storage, ImagingService, ingestion, derivation, QC worker (Product B)
  annotation/    blinding, workflow, agreement                                       (Product C)
  pipeline/      snapshot, temporal utilities, labels, splits, cohort flow, builder (Product D)
  ml/            config, data, models (clinical, CNN/ViT, fusion, U-Net), training, test lock, replay (E)
  evaluation/    metrics, calibration, early warning, survival, clustered models, plots, reports, registry (F)
  interpret/     Grad-CAM, attributions                                              (Product G)
  monitoring/    DQ metrics, site reports                                            (Product I)
  integrations/  FHIR adapter, XNAT client + repository + sync
  synthetic.py   SYNTHETIC data generator
frontend/        Next.js (TypeScript): DQ dashboard, timeline/viewer, annotation, adjudication (H, I, C)
configs/         data dictionary, QC thresholds, blinding profiles, dataset specs, ML configs, SAP config
migrations/      Alembic (0001 initial, 0002 analysis registry, 0003 XNAT)
infrastructure/  Dockerfiles, MinIO bucket policy (Object Lock), nginx TLS example, backup script
docs/            requirements, architecture, data model, data dictionary, security/governance,
                 reproducibility, scientific risks, XNAT integration, screenshots
sample_data/     import templates + watch-folder example (SYNTHETIC)
tests/           72 tests: API/RBAC, DB, validation, QC, leakage, evaluation, pipeline/ML, XNAT (mocked)
```

## Documentation
[Requirements](docs/PRODUCT_REQUIREMENTS.md) · [Architecture](docs/ARCHITECTURE.md) · [Data model](docs/DATA_MODEL.md) ·
[Data dictionary CSV](docs/DATA_DICTIONARY.csv) / [MD](docs/data_dictionary.md) · [Security & governance](docs/SECURITY_GOVERNANCE.md) ·
[Reproducibility](docs/REPRODUCIBILITY.md) · [Scientific risks](docs/SCIENTIFIC_RISKS.md) · [XNAT](docs/XNAT_INTEGRATION.md)

Adding a clinical variable: edit `configs/data_dictionary.yaml`, then run `irtcri dictionary-docs`. No database
migration is needed; measurements are stored in long format.
