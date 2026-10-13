# Reproducibility and research integrity

Goal: any manuscript number can be regenerated years later from recorded inputs.

| Element | Mechanism | Where |
|---|---|---|
| Code version | git commit (with `-dirty` flag) recorded in every manifest, model card, QC document and analysis record; baked into containers as `IRTCRI_GIT_COMMIT` | `core/provenance.py` |
| Environment | pinned lock files; key package versions + hash recorded in manifests | `requirements/lock-core.txt`, `requirements/lock-ml.txt`, `frontend/package-lock.json` |
| Configuration | YAML files under version control; full spec embedded in manifests / model cards / analysis records with SHA-256 | `configs/` |
| Database schema | Alembic migration history (`0001` → `0003`), `alembic check` in CI | `migrations/` |
| Raw data | write-once objects, SHA-256 per file, raw layer snapshot incl. voided rows | object store, `datasets/<name>/<ver>/raw` |
| Derived images | parent checksum, parameters, preprocessing version (hash of parameters), code version | `derived_image` table |
| Datasets | deterministic build (sorted keys, seeded patient-level splits ordered by Study ID), manifest with counts, prevalence, missingness, checksums, data cut-off time; `lock-dataset` makes it read-only | `pipeline/builder.py` |
| Models | run directory with config, seed, dataset checksum + split fingerprint, model card, validation predictions, training history; MLflow optional | `var/runs/...` |
| Test set | evaluation only via `evaluate-test --confirm-final`; append-only ledger shows every access | `test_access_ledger.jsonl` |
| Analyses | `irtcri register-analysis` stores paper_id, analysis_version, dataset name/version/checksum/locked, git_commit, statistical config (+hash), model version (+card hash), environment, output-file checksums, date_created; immutable | `analysis_record` table + `analysis_record.json` next to outputs |
| Logs | audit trail in DB; training metrics JSONL; import batches with per-row issues | |

## Manuscript workflow
```bash
irtcri build-dataset configs/datasets/primary_early_warning.yaml   # version from the spec
irtcri lock-dataset irtcri_early_warning 1.0.0
irtcri train configs/ml/f0_clinical_thermal_summary_logreg.yaml --set dataset_root=...
irtcri evaluate-test <run_dir> --confirm-final                     # once, pre-specified model
irtcri study-analysis <dataset_root> --out results/paper1
irtcri register-analysis IRTCRI-P1 1.0 --dataset-root <root> --stat-config configs/analysis/primary_sap_v0.yaml \
    --model-run <run_dir> --outputs results/paper1 --analyst STAT01
```

## Regenerating the lock files
```bash
uv pip compile pyproject.toml --extra dev --extra postgres --extra s3 --python-version 3.12 -o requirements/lock-core.txt
uv pip compile pyproject.toml --extra dev --extra postgres --extra s3 --extra ml --extra tracking \
    --python-version 3.12 -o requirements/lock-ml.txt
```
For CPU-only servers install torch from the PyTorch CPU index (see `infrastructure/docker/Dockerfile.api`, target `ml`).

## Known limits
- GPU training can be non-deterministic despite deterministic settings. Record the hardware, and expect small numeric differences.
- Pretrained weights must be cached and versioned. If a download fails, training falls back to random initialisation and the model card records `pretrained_weights_loaded: false`.
- Code version is reliable only if analyses run from a clean, committed tree. A `-dirty` suffix in a record means it is not reproducible.
