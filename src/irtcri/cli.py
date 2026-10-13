"""Command-line interface: ``irtcri <command> ...`` (see ``irtcri --help``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _session():
    from irtcri.db.session import session_factory
    return session_factory()()


def cmd_init_db(a):
    from irtcri.core.config import get_settings
    if get_settings().env in ("staging", "prod"):
        sys.exit("Use `alembic upgrade head` in staging/prod.")
    from irtcri.db.session import create_all
    create_all()
    print("database tables created (dev/test)")


def cmd_create_user(a):
    from irtcri.core.security import SYSTEM_PRINCIPAL
    from irtcri.hub import schemas as S
    from irtcri.hub import services as svc
    s = _session()
    u, token = svc.create_user(s, SYSTEM_PRINCIPAL, S.UserIn(username=a.username, role=a.role, site_code=a.site))
    s.commit()
    print(json.dumps({"username": u.username, "role": u.role, "token": token}))
    print("Store the token securely - it is not shown again.", file=sys.stderr)


def cmd_dictionary_docs(a):
    from irtcri.schemas.dictionary import dictionary_csv, dictionary_markdown
    Path(a.out).write_text(dictionary_markdown())
    Path(a.csv).write_text(dictionary_csv())
    print(f"wrote {a.out} and {a.csv}")


def cmd_synthetic(a):
    from irtcri.synthetic import SynthConfig, generate
    s = _session()
    print(json.dumps(generate(s, SynthConfig(n_patients=a.n_patients, seed=a.seed, rgb=not a.no_rgb))))


def cmd_import(a):
    from irtcri.core.security import SYSTEM_PRINCIPAL
    from irtcri.hub.importers import import_file
    s = _session()
    rep = import_file(s, SYSTEM_PRINCIPAL, a.kind, Path(a.file).read_bytes(), Path(a.file).name, a.site, a.timezone,
                      dry_run=not a.commit)
    s.commit()
    b = rep.batch
    print(json.dumps({"batch": b.id, "dry_run": b.dry_run, "accepted": b.rows_accepted, "flagged": b.rows_flagged,
                      "rejected": b.rows_rejected, "issues": rep.issues[:50]}, indent=2, default=str))


def cmd_watch(a):
    from irtcri.core.security import SYSTEM_PRINCIPAL
    from irtcri.imaging.watch import scan_folder
    s = _session()
    print(json.dumps(scan_folder(s, SYSTEM_PRINCIPAL, a.folder), indent=2))


def cmd_build(a):
    from irtcri.pipeline.builder import build_dataset
    m = build_dataset(_session(), a.spec, a.out_root, derive_images=not a.no_images)
    print(json.dumps({"root": m["root"], "counts": m["counts"], "prevalence": m["prevalence"],
                      "checksum": m["checksum"]}, indent=2))


def cmd_lock(a):
    from irtcri.pipeline.builder import lock_dataset
    print(json.dumps(lock_dataset(_session(), a.name, a.version, a.out_root), indent=2))


def cmd_train(a):
    from irtcri.ml.config import load_experiment
    from irtcri.ml.train import train_experiment
    overrides = dict(kv.split("=", 1) for kv in a.set or [])
    overrides = {k: json.loads(v) if v[:1] in "[{0123456789-tf" else v for k, v in overrides.items()}
    cfg = load_experiment(a.config, overrides)
    run = train_experiment(cfg)
    card = json.loads((run / "model_card.json").read_text())
    vm = card["validation_metrics"]
    print(json.dumps({"run_dir": str(run), "threshold": card["threshold"],
                      "val_auroc": vm["auroc"], "val_sensitivity": vm["sensitivity"],
                      "val_specificity": vm["specificity"]}, indent=2))


def cmd_eval_test(a):
    from irtcri.ml.testlock import evaluate_locked_test
    res = evaluate_locked_test(a.run_dir, confirm_final=a.confirm_final, split=a.split, n_boot=a.n_boot)
    print(json.dumps({"access": res["test_access"], "image_auroc": res["image_level"]["auroc"],
                      "early_warning": res["early_warning"]["summary"].get("event_sensitivity")}, indent=2))


def cmd_eval_val(a):
    import pandas as pd

    from irtcri.evaluation.report import evaluate_predictions
    from irtcri.ml.testlock import episodes_for_evaluation
    from irtcri.ml.train import LoadedModel
    m = LoadedModel(a.run_dir)
    pred = pd.read_parquet(Path(a.run_dir) / "val_predictions.parquet")
    ep = episodes_for_evaluation(m.card["dataset"]["root"])
    pred = pred.merge(ep[["episode_id", "catheter_type"]], on="episode_id", how="left")
    res = evaluate_predictions(pred, ep[ep["episode_id"].isin(pred["episode_id"])], m.threshold,
                               Path(a.run_dir) / "validation_evaluation", m.card["model_name"], "validation",
                               n_boot=a.n_boot)
    print((Path(a.run_dir) / "validation_evaluation" / "evaluation.md").read_text())
    return res


def cmd_replay(a):
    from irtcri.core.security import SYSTEM_PRINCIPAL
    from irtcri.ml.infer import replay
    n = replay(_session(), SYSTEM_PRINCIPAL, a.run_dir, tuple(a.splits), gradcam=not a.no_gradcam, limit=a.limit)
    print(f"stored {n} retrospective predictions")


def cmd_study(a):
    from irtcri.evaluation.study_analysis import run_study_analysis
    res = run_study_analysis(a.dataset_root, a.out)
    print(res.get("table1_markdown"))
    print(json.dumps({k: res.get(k) for k in ("km_median_h", "logrank_catheter_type")}, indent=2))


def cmd_site_report(a):
    from irtcri.monitoring.site_report import site_report_html, site_report_markdown
    from irtcri.pipeline.snapshot import load_tables
    s = _session()
    from irtcri.hub.services import get_site
    site = get_site(s, a.site)
    t = load_tables(s, site_ids=[site.id])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"site_report_{a.site}_{a.month}.md").write_text(site_report_markdown(t, a.site, a.month))
    (out / f"site_report_{a.site}_{a.month}.html").write_text(site_report_html(t, a.site, a.month))
    print(f"wrote reports to {out}")


def cmd_register_analysis(a):
    from irtcri.core.security import SYSTEM_PRINCIPAL, Principal, Role
    from irtcri.evaluation.registry import register_analysis
    s = _session()
    who = Principal(None, a.analyst or SYSTEM_PRINCIPAL.username, Role.STATISTICIAN)
    rec = register_analysis(s, who, a.paper_id, a.analysis_version, a.dataset_root, a.stat_config, a.outputs,
                            a.model_run, a.description, allow_unlocked=a.allow_unlocked)
    s.commit()
    print(json.dumps({"id": rec.id, "paper_id": rec.paper_id, "analysis_version": rec.analysis_version,
                      "dataset": f"{rec.dataset_name}@{rec.dataset_version}", "dataset_locked": rec.dataset_locked,
                      "git_commit": rec.git_commit, "statistical_config_sha256": rec.statistical_config_sha256,
                      "model_version": rec.model_version, "date_created": rec.date_created.isoformat(),
                      "n_outputs": len(rec.outputs or {})}, indent=2))


def cmd_xnat_check(a):
    from irtcri.imaging.repository import get_xnat_repository
    repo = get_xnat_repository()
    if repo is None:
        sys.exit("XNAT_ENABLED is false")
    repo.check_project()
    print(json.dumps({"xnat": repo.cfg.url, "project": repo.cfg.project_id, "status": "reachable"}))


def cmd_xnat_push(a):
    from scripts.migrate_images_to_xnat import main as mig
    sys.exit(mig((["--dry-run"] if not a.commit else []) + (["--limit", str(a.limit)] if a.limit else [])
                 + (["--report", a.report] if a.report else [])))


def cmd_xnat_pull(a):
    from irtcri.core.security import SYSTEM_PRINCIPAL
    from irtcri.integrations.xnat.sync import pull_from_xnat
    rep = pull_from_xnat(_session(), SYSTEM_PRINCIPAL, dry_run=not a.commit)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in rep.items()}, indent=2))
    for e in rep["errors"][:20]:
        print(json.dumps(e))


def cmd_qc_worker(a):
    from irtcri.core.security import SYSTEM_PRINCIPAL
    from irtcri.imaging.qc_worker import run_qc_worker
    print(json.dumps(run_qc_worker(_session(), SYSTEM_PRINCIPAL, only_unpublished=not a.all, limit=a.limit),
                     indent=2))


def cmd_publish_annotations(a):
    from irtcri.imaging.publish import publish_annotations
    s = _session()
    n = publish_annotations(s)
    s.commit()
    print(f"published annotation documents for {n} images")


def cmd_serve(a):
    import uvicorn
    uvicorn.run("irtcri.hub.app:app", host=a.host, port=a.port, reload=a.reload)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="irtcri", description="IRT-in-CRI research platform")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create tables (dev/test only)").set_defaults(fn=cmd_init_db)
    c = sub.add_parser("create-user")
    c.add_argument("username")
    c.add_argument("role")
    c.add_argument("--site")
    c.set_defaults(fn=cmd_create_user)
    c = sub.add_parser("dictionary-docs")
    c.add_argument("--out", default="docs/data_dictionary.md")
    c.add_argument("--csv", default="docs/DATA_DICTIONARY.csv")
    c.set_defaults(fn=cmd_dictionary_docs)
    c = sub.add_parser("synthetic", help="generate SYNTHETIC demo data")
    c.add_argument("--n-patients", type=int, default=20)
    c.add_argument("--seed", type=int, default=7)
    c.add_argument("--no-rgb", action="store_true")
    c.set_defaults(fn=cmd_synthetic)
    c = sub.add_parser("import", help="CSV/XLSX import (dry run unless --commit)")
    c.add_argument("kind")
    c.add_argument("file")
    c.add_argument("--site")
    c.add_argument("--timezone")
    c.add_argument("--commit", action="store_true")
    c.set_defaults(fn=cmd_import)
    c = sub.add_parser("watch-folder")
    c.add_argument("folder")
    c.set_defaults(fn=cmd_watch)
    c = sub.add_parser("build-dataset")
    c.add_argument("spec")
    c.add_argument("--out-root")
    c.add_argument("--no-images", action="store_true")
    c.set_defaults(fn=cmd_build)
    c = sub.add_parser("lock-dataset")
    c.add_argument("name")
    c.add_argument("version")
    c.add_argument("--out-root")
    c.set_defaults(fn=cmd_lock)
    c = sub.add_parser("train")
    c.add_argument("config")
    c.add_argument("--set", nargs="*", help="override config values, e.g. training.epochs=3")
    c.set_defaults(fn=cmd_train)
    c = sub.add_parser("evaluate-val")
    c.add_argument("run_dir")
    c.add_argument("--n-boot", type=int, default=1000)
    c.set_defaults(fn=cmd_eval_val)
    c = sub.add_parser("evaluate-test", help="ONE-TIME locked test evaluation")
    c.add_argument("run_dir")
    c.add_argument("--confirm-final", action="store_true")
    c.add_argument("--split", default="test")
    c.add_argument("--n-boot", type=int, default=2000)
    c.set_defaults(fn=cmd_eval_test)
    c = sub.add_parser("replay", help="store retrospective predictions for the dashboard")
    c.add_argument("run_dir")
    c.add_argument("--splits", nargs="+", default=["val"])
    c.add_argument("--no-gradcam", action="store_true")
    c.add_argument("--limit", type=int)
    c.set_defaults(fn=cmd_replay)
    c = sub.add_parser("study-analysis")
    c.add_argument("dataset_root")
    c.add_argument("--out", default="var/reports/study")
    c.set_defaults(fn=cmd_study)
    c = sub.add_parser("site-report")
    c.add_argument("site")
    c.add_argument("month", help="YYYY-MM")
    c.add_argument("--out", default="var/reports/sites")
    c.set_defaults(fn=cmd_site_report)
    c = sub.add_parser("register-analysis", help="freeze provenance of a manuscript analysis")
    c.add_argument("paper_id")
    c.add_argument("analysis_version")
    c.add_argument("--dataset-root", required=True)
    c.add_argument("--stat-config", required=True)
    c.add_argument("--outputs")
    c.add_argument("--model-run")
    c.add_argument("--description")
    c.add_argument("--analyst", help="pseudonymous analyst code recorded in the registry")
    c.add_argument("--allow-unlocked", action="store_true", help="drafts only")
    c.set_defaults(fn=cmd_register_analysis)
    sub.add_parser("xnat-check", help="verify XNAT connectivity and project access").set_defaults(fn=cmd_xnat_check)
    c = sub.add_parser("xnat-push", help="copy hub images to XNAT (dry run unless --commit)")
    c.add_argument("--commit", action="store_true")
    c.add_argument("--limit", type=int)
    c.add_argument("--report")
    c.set_defaults(fn=cmd_xnat_push)
    c = sub.add_parser("xnat-pull", help="register images uploaded directly to XNAT (dry run unless --commit)")
    c.add_argument("--commit", action="store_true")
    c.set_defaults(fn=cmd_xnat_pull)
    c = sub.add_parser("qc-worker", help="run QC and publish QC documents (to XNAT when enabled)")
    c.add_argument("--all", action="store_true", help="re-run even if an up-to-date QC document exists")
    c.add_argument("--limit", type=int)
    c.set_defaults(fn=cmd_qc_worker)
    sub.add_parser("xnat-publish-annotations").set_defaults(fn=cmd_publish_annotations)
    c = sub.add_parser("serve")
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--port", type=int, default=8000)
    c.add_argument("--reload", action="store_true")
    c.set_defaults(fn=cmd_serve)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
