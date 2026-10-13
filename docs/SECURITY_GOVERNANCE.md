# Security, privacy and governance

> **No compliance claim.** This platform was *designed with* Saudi data-protection and research-ethics requirements in mind: the Personal Data Protection Law (PDPL) and its implementing regulations (SDAIA), National Cybersecurity Authority controls, national research-ethics regulations (NCBE), and institutional IRB and hospital IT policies. **It has not been formally reviewed against them.** Compliance can only be confirmed by the institution's Data Protection Officer, information-security office, IRB/ethics committee and legal counsel. Where SFDA rules on software or AI medical devices could apply to a future clinical use, regulatory review is required before any such use.

## 1. Threat overview

| Asset | Threat | Main mitigations |
|---|---|---|
| Pseudonymised clinical data | unauthorised access, insider browsing, cross-site access | RBAC, site scoping, audit of reads of sensitive views, least-privilege roles |
| Thermal/RGB images | re-identification (faces, tattoos, names in filenames/EXIF) | Study-ID-only object keys and XNAT labels; original filenames discarded; acquisition protocol restricts field of view (**INST**) |
| Raw data integrity | tampering, accidental overwrite/deletion | write-once objects (read-only files / S3 Object Lock), SHA-256 verified on every read, append-only audit trail |
| Research integrity | test-set reuse, silent re-analysis, leakage | test-set ledger, locked datasets, analysis registry, leakage assertions |
| Credentials/tokens | leakage via code, logs, images | env-var configuration, hashed tokens, no secrets in images (BuildKit secrets), PHI-free logs |
| Model outputs | misuse as diagnosis | investigational wording, prospective alerts behind feature flag + approval reference + Admin-locked model |
| Availability | server loss, ransomware | encrypted backups + WORM raw bucket, documented restore drills (**INST**) |

## 2. IMPLEMENTED TECHNICAL CONTROLS
(Each is in code and, where marked †, covered by automated tests.)

**Identity and access**
- Roles Admin, DataManager, SiteResearcher, Annotator, Statistician, MLResearcher, ClinicianReviewer and ReadOnlyAuditor, with an explicit permission matrix (`core/security.py`)†.
- Site scoping: SiteResearchers can read and write only their own site†.
- Annotators see only assigned images, through server-enforced blinding profiles†.
- Bearer tokens are 256-bit random, stored only as SHA-256 hashes, and expire (`IRTCRI_TOKEN_TTL_DAYS`). Users can be deactivated.
- One-time bootstrap token for the first Admin, refused once any user exists†.

**Pseudonymisation and data minimisation**
- Only pseudonymous Study IDs (`SITE-NNNN`). Name/MRN/DOB fields are rejected by the schema (`extra="forbid"`)†. Age is stored instead of date of birth.
- Uploaded filenames are never stored; objects are keyed by Study ID + checksum†.
- XNAT subject labels must match the Study-ID pattern†.
- Blinded annotation views mask identity and time†.

**Integrity and auditability**
- Append-only `audit_event` for creates, corrections (before/after), voids, withdrawals, QC overrides, assignments, adjudications, exports, imports, sensitive reads, model status changes and XNAT migrations†.
- No hard deletes through the software. Voids and corrections require a reason; optimistic concurrency via `record_version`†.
- Raw images write-once (local read-only files; S3 Object Lock in compliance mode in `infrastructure/minio/init-buckets.sh`). SHA-256 verified on read; automatic fallback between backends†.

**Withdrawal**
- New data after withdrawal is refused†.
- Participants who withdrew without consent to further use are excluded from all datasets and exports, while their records stay for audit†.

**Logging**
- Structured JSON access logs contain route template, status, duration and request id only.
- No bodies, query strings, tokens or filenames are logged (`core/logging.py`).

**Transport and storage**
- Example TLS reverse-proxy configuration with HSTS (`infrastructure/nginx`).
- Security headers on API and UI. Database, object-store and UI ports are bound to localhost in compose.
- Optional SSE header for S3 (`IRTCRI_S3_SSE`). Encrypted (GPG) database backup script.

**Secrets**
- All credentials come from environment variables. Staging/prod refuse SQLite and missing S3/XNAT credentials†.
- `.env` is git-ignored. Configuration objects never print passwords†.

**Model governance**
- Prospective alerts require `IRTCRI_PROSPECTIVE_ALERTS_ENABLED`, an approval reference, and a model locked by an Admin with an approval reference†.
- Alert wording is fixed ("Elevated model-estimated risk…") with the disclaimer on every output†.

**Research integrity**
- Test-set lock and access ledger†, locked datasets with checksums†, analysis registry†, deterministic splits†.

**Retention setting**
- `IRTCRI_DATA_RETENTION_YEARS` (default 15) and the Object Lock retention period (`RAW_RETENTION_DAYS`) are configuration. Their *values* are an institutional decision.

## 3. ITEMS REQUIRING INSTITUTIONAL / LEGAL VALIDATION

| # | Item | Owner |
|---|---|---|
| 1 | Lawful basis, consent wording (incl. imaging, data use after withdrawal, future research, data sharing between centres) and IRB approval for each site | PI, IRB/ethics committee |
| 2 | PDPL assessment: classification of health data as sensitive, records of processing, DPIA, data-subject rights procedures (access/correction/erasure vs research exemptions), breach notification process | DPO, legal |
| 3 | **Data residency**: hosting location of database, object storage, backups, XNAT and any cloud service; cross-border transfer restrictions; whether international collaborators may receive pseudonymised exports | DPO, legal, IT |
| 4 | Whether pseudonymised data and images count as personal data for each recipient; re-identification-key custody at sites | DPO |
| 5 | Mapping of the hosting environment to NCA controls and hospital security policy; penetration test; vulnerability management; OS hardening | Information-security office |
| 6 | Institutional SSO (OIDC/SAML) and MFA to replace bearer tokens before multi-user production use | IT |
| 7 | Encryption at rest (disk/volume, KMS for MinIO SSE), key management, backup encryption keys and restore drills | IT |
| 8 | Retention period, archival and secure-destruction procedure at study end; procedure for any legally required erasure (DBA-level, documented, audited) | PI, DPO, records management |
| 9 | Image acquisition SOP: field of view excluding face and identifiable marks, camera settings, screen-lock on acquisition devices, device storage wiping | PI, site leads |
| 10 | Data-sharing / data-processing agreements between centres and with any vendor (XNAT hosting, cloud) | Legal |
| 11 | Regulatory classification before **any** prospective use that could influence care (SFDA software/AI medical-device rules), and a clinical-safety case | Regulatory affairs, PI |
| 12 | Approval of the statistical analysis plan, reference-standard definitions and adjudication charter | PI, statistician, endpoint committee |
| 13 | Access review cadence (quarterly) and account de-provisioning procedure | DataManager, IT |

## 4. Operational security checklist (before real data)
- [ ] Deploy on institution-controlled infrastructure, in-Kingdom, behind TLS; change every default in `.env.example`.
- [ ] Remove `IRTCRI_BOOTSTRAP_ADMIN_TOKEN` after creating the first Admin.
- [ ] Enable MinIO KMS / disk encryption; verify Object Lock on the raw bucket.
- [ ] Configure and test encrypted backups and a restore drill.
- [ ] Complete items 1–13 above, as applicable, and record approvals in the trial master file.
