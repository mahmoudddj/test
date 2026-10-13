# IRT-in-CRI research platform — product requirements

Version 0.2. Owner: data science / data engineering, IRT-in-CRI Study.
Status legend: **DONE** = implemented and covered by automated tests; **PARTIAL** = implemented with stated limits; **OPEN** = not implemented; **INST** = requires institutional/legal action, not software.

## 1. Purpose and scope

Research software for a funded multicentre study (~100 participants; ICU, dialysis and oncology settings) evaluating infrared thermography (IRT) for **earlier detection of catheter-related / exit-site infection (CRI/ESI)** than standard clinical assessment. The software must:

- collect longitudinal clinical and imaging data;
- guarantee data quality and provenance;
- produce leakage-free analysis datasets;
- train and evaluate models rigorously;
- support a later retrospective, then prospective, *research* pilot.

**Out of scope:** clinical decision support for patient management, autonomous diagnosis, EHR replacement, regulated-device claims. Every model output is labelled *investigational* (`irtcri.INVESTIGATIONAL_DISCLAIMER`).

## 2. Users and roles

| Role | Purpose | Key permissions (`core/security.py`) |
|---|---|---|
| Admin | system owner | everything; locks validated models |
| DataManager | multicentre data management | all sites; queries; corrections; exports; annotation assignment |
| SiteResearcher | site data entry | **own site only**: enrol, enter clinical data, upload images, correct with reason |
| Annotator | blinded image reader | only assigned images, only the fields released by the blinding profile |
| ClinicianReviewer | adjudicator / endpoint committee | adjudicate; read clinical data; view predictions |
| Statistician | analysis | read + export, all sites; analysis registry |
| MLResearcher | modelling | read + export; register models; store predictions |
| ReadOnlyAuditor | monitoring/audit | audit trail + DQ dashboard only |

## 3. Functional requirements

### A. Data Hub (Release 1)
| ID | Requirement | Status |
|---|---|---|
| A1 | Sites, screening log, enrolment with pseudonymous Study IDs (`SITE-NNNN`); identifiers (names, MRN, DOB) are rejected by schema (`extra="forbid"`) | DONE |
| A2 | Encounters and **multiple catheter episodes per patient** (type, site, tunnelled, insertion/removal/end-of-follow-up times, imaging interval) | DONE |
| A3 | Longitudinal observations and laboratory results in long format, validated against the data dictionary (units converted, impossible values rejected, implausible values flagged + automatic data query, cross-field rules, explicit missing codes) | DONE |
| A4 | Medications/antibiotics, microbiology (collection/report times), clinical exit-site assessments, outcomes (clinical recognition time mandatory for events), protocol deviations, data queries | DONE |
| A5 | Every timestamp timezone-aware and stored in UTC; `event_time` / `source_time` / `recorded_time` separated | DONE |
| A6 | Corrections field-by-field with reason and optimistic concurrency; voiding instead of deletion; before/after values in the audit trail | DONE |
| A7 | CSV/XLSX bulk import (long or wide; units in headers; site-timezone localisation), **dry run by default**, per-row issue report, file checksum | DONE |
| A8 | REST API with OpenAPI docs (`/docs`) | DONE |
| A9 | FHIR R4 Observation adapter through the same validation path (optional) | PARTIAL (Observation only) |
| A10 | Consent withdrawal: block new data after withdrawal; keep records for audit; exclude from datasets/exports when further use was not consented | DONE |
| A11 | Pseudonymised snapshot export (parquet + CSV + data dictionary + manifest) | DONE |

### B. Image acquisition and QC (Release 1)
| ID | Requirement | Status |
|---|---|---|
| B1 | Upload, watch-folder (sidecar JSON or filename convention), camera-export adapter; SDK adapter *interface* only (no fabricated device support) | DONE |
| B2 | Thermal readers: vendor CSV export, `.npy`, 16-bit/float TIFF with declared scale; palette images accepted but flagged non-radiometric; radiometric-JPEG via pluggable validated decoder | DONE (plugin slot only for R-JPEG) |
| B3 | Required acquisition metadata: Study ID, episode, timestamp, site, catheter type, camera make/model, device id, calibration, ambient/humidity, distance, view, operator code, resolution, protocol version, raw checksum | DONE |
| B4 | Configurable QC (`configs/qc/thermal_qc.yaml`) with PASS/WARNING/FAIL and reasons: corruption, format, size, dimensions, physical range (unit errors), extreme/abnormal statistics, saturation, NaNs, blur/exposure (RGB), missing/implausible metadata, timestamp rules (future, before insertion, after removal, EXIF mismatch), exact and near duplicates | DONE |
| B5 | Nothing deleted; human QC override stored separately with reason; QC re-run keeps history | DONE |
| B6 | Raw originals write-once (filesystem read-only / S3 Object Lock); derivatives stored separately with parent checksum, parameters, preprocessing and code version | DONE |
| B7 | Original filenames never stored (they may contain names/MRNs); objects keyed by Study ID + checksum | DONE |
| B8 | Thermal–RGB pairing; affine registration from control points | DONE (automatic multimodal registration OPEN) |
| B9 | Optional XNAT imaging repository (dual-write, pull, QC/derived write-back, migration) | DONE (mocked; live validation pending) |

### C. Annotation and adjudication (Release 2)
| ID | Requirement | Status |
|---|---|---|
| C1 | Web annotation: thermal + RGB view, insertion-site point, polygon ROI, reference-skin ROI, 7 infection categories, confidence, image-quality flag, comments | DONE (free-hand segmentation masks OPEN) |
| C2 | Server-enforced, configurable blinding profiles; opaque task codes; model outputs never shown to readers | DONE |
| C3 | Independent reader 1 / reader 2 (same person cannot hold both roles), versioned annotations, automatic agreement, adjudication by a non-reader, final reference label | DONE |
| C4 | Inter-rater reliability (kappa, weighted kappa, patient-cluster bootstrap CI) and pair export | DONE |
| C5 | Reference labels never generated by the model | DONE (by design) |

### D. Dataset builder (Release 3)
| ID | Requirement | Status |
|---|---|---|
| D1 | Layers: raw (incl. voided rows) → validated → longitudinal → ML → locked (read-only + checksums) | DONE |
| D2 | Manifest: version, timestamps, data cut-off, git commit, environment, sites, inclusion logic (full spec), counts, prevalence, missingness, preprocessing, file checksums | DONE |
| D3 | Leakage control: availability time per variable (lab report time + lag); window features only from available data; automatic future-leakage assertion; post-recognition samples excluded | DONE |
| D4 | Patient-level splits, stratified by event and site, deterministic (ordered by Study ID); optional held-out sites; leakage assertion | DONE |
| D5 | Temporal utilities: as-of joins, windows, resampling, slopes, missingness indicators, time-to-event construction, within-episode thermal baseline change | DONE |
| D6 | Cohort flow (screened → eligible → enrolled → exclusions with reasons → splits → events) as CSV/Markdown | DONE |
| D7 | Exports: parquet, CSV, metadata JSON | DONE |

### E–G. ML, evaluation, interpretability (Release 3)
| ID | Requirement | Status |
|---|---|---|
| E1 | Families F0 (clinical), F1 (thermal), F2 (RGB), F3 (thermal+RGB), F4 (clinical+thermal), F5 (all); ResNet/EfficientNet/ViT backbones with 1-channel adaptation; simple CNN; U-Net | DONE |
| E2 | Config files, seeds and deterministic algorithms, patient-level splits, augmentation (geometric; no thermal photometric jitter), class-imbalance options, early stopping, checkpoints, tracking (MLflow optional), model card | DONE |
| E3 | Threshold and calibration selected on validation only; **test set locked**: explicit confirmation, dataset/split fingerprint check, append-only access ledger | DONE |
| F1 | Sensitivity, specificity, PPV, NPV, F1, AUROC, AUPRC, Brier, confusion, calibration curve/slope/intercept, ECE, threshold tables, **patient-cluster bootstrap CIs** | DONE |
| F2 | Event-level early-warning evaluation: alert rule, lead time vs conventional recognition, sensitivity at ≥0/6/12/24/48 h lead, false alerts per catheter-day, episode specificity, alert PPV | DONE |
| F3 | Kaplan–Meier (Greenwood/log-log CI), log-rank, Cox with cluster-robust SE, GEE, mixed models, guard against naive logistic regression on repeated data | DONE |
| F4 | Site- and catheter-type-stratified results; exploratory subgroups labelled | DONE |
| F5 | Publication tables/figures (PNG 300 dpi + PDF), Table 1, report Markdown/JSON | DONE |
| G1 | Grad-CAM (CNN), linear contributions / SHAP (if installed) / occlusion (labelled), stored per prediction with caveats | DONE |

### H–I. Dashboards (Releases 2 and 4)
| ID | Requirement | Status |
|---|---|---|
| H1 | Patient timeline: insertion/removal, images (QC-coloured), assessments, temperature, antibiotics, microbiology, model risk with threshold, reference diagnosis | DONE |
| H2 | Viewer: thermal render with temperature-on-hover, RGB, Grad-CAM overlay, probability, model version, research-alert wording, disclaimer | DONE |
| H3 | Retrospective replay; prospective alerts only with feature flag + approval reference + `validated_locked` model (Admin-only lock) | DONE |
| I1 | DQ dashboard: recruitment, episodes, images per patient, expected vs collected timepoints, missing timepoints, clinical completeness, missing outcomes, queries, QC, annotation progress, prevalence, latency, deviations, duplicates, flagged values, cross-site comparison, drill-down by Study ID | DONE |
| I2 | Monthly site QC report (Markdown/HTML) | DONE |

### R. Research integrity (cross-cutting)
| ID | Requirement | Status |
|---|---|---|
| R1 | Analysis registry: paper_id, analysis_version, dataset (+checksum, locked), git_commit, statistical config (+hash), model version (+card hash), environment, output checksums, date_created; append-only | DONE |
| R2 | Lock files (`requirements/lock-*.txt`, `frontend/package-lock.json`), migration history, deterministic builds | DONE |
| R3 | Synthetic data generator (multi-site, catheter types, repeated observations, missing values, duplicates, QC failures, infection outcomes), all marked SYNTHETIC | DONE |

## 4. Non-functional requirements
- Runs on a laptop (SQLite + local files) and on a server (`docker compose up`: PostgreSQL, MinIO, API, UI). No cloud dependency, open-source components only.
- No PHI in logs (structured access logs contain route templates, status and duration only).
- Reproducible installation (pinned lock files). Tests run in CI on PostgreSQL.
- Prospective/clinical deployment additionally requires the items in `SECURITY_GOVERNANCE.md` §3.

## 5. Explicitly not done in this phase
Institutional SSO, automatic multimodal registration, segmentation-mask painting UI, FHIR resources beyond Observation, camera SDK integrations, XNAT Container Service, competing-risk (Aalen–Johansen) estimator, live-XNAT validation.
