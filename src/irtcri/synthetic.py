"""SYNTHETIC study data generator - for development, testing and demonstrations ONLY.

Produces a plausible multicentre dataset through the *same* service layer used
for real data (validation, QC, audit). The signal model is deliberately
simple and must never be used to draw scientific conclusions:

* infection episodes: a local thermal hotspot starts rising ~``preclinical_h``
  hours before conventional clinical recognition; systemic markers rise later;
* non-infected episodes: transient post-insertion warmth and random noise;
* sites differ slightly in camera offset and ambient temperature;
* a small fraction of images are corrupted, blurred or duplicated to exercise QC.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from sqlalchemy.orm import Session

from irtcri.core.security import SYSTEM_PRINCIPAL, Principal
from irtcri.hub import schemas as S
from irtcri.hub import services as svc
from irtcri.imaging.ingest import ingest_image

SITES = [
    {"site_code": "S01", "name": "SYNTHETIC University Hospital ICU", "timezone": "Europe/Berlin",
     "setting": "ICU", "catheters": ["CVC", "CVC", "HD_CVC"], "camera_offset": 0.0, "ambient": 22.5},
    {"site_code": "S02", "name": "SYNTHETIC Dialysis Centre", "timezone": "Africa/Cairo",
     "setting": "dialysis", "catheters": ["HD_CVC"], "camera_offset": 0.4, "ambient": 24.5},
    {"site_code": "S03", "name": "SYNTHETIC Oncology Day Unit", "timezone": "Asia/Riyadh",
     "setting": "oncology", "catheters": ["PORT", "PORT", "CVC"], "camera_offset": -0.3, "ambient": 21.5},
]
ORGANISMS = ["Staphylococcus aureus", "Staphylococcus epidermidis", "Pseudomonas aeruginosa"]
INSERTION = {"CVC": ["internal_jugular_right", "subclavian_right", "femoral_right"],
             "HD_CVC": ["internal_jugular_right", "internal_jugular_left"],
             "PORT": ["chest_wall_right", "chest_wall_left"]}


@dataclass
class SynthConfig:
    n_patients: int = 20
    seed: int = 7
    event_rate: float = 0.28
    imaging_interval_h: float = 12.0
    vitals_interval_h: float = 6.0
    preclinical_h: float = 48.0            # thermal signal precedes recognition by up to this many hours
    hotspot_max_c: float = 3.0             # local excess temperature at recognition
    image_shape: tuple[int, int] = (120, 160)
    rgb: bool = True
    p_missed_image: float = 0.07
    p_corrupt: float = 0.015
    p_blur: float = 0.03
    p_duplicate: float = 0.01
    p_duplicate_observation: float = 0.01   # double data entry (exercises DQ duplicate detection)
    p_explicit_missing: float = 0.02        # 'ND' (not done) entries instead of silent gaps
    start: datetime = datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc)
    study_days: int = 120


def _thermal(rng, shape, amp, cx, cy, base, ambient, spread=9.0) -> bytes:
    h, w = shape
    yy, xx = np.mgrid[:h, :w]
    body = base + 0.8 * np.exp(-((xx - w * 0.55) ** 2) / (2 * (w * 0.5) ** 2)) + 0.03 * (ambient - 22)
    bg = (xx < w * 0.08)  # strip of background (cooler, near ambient)
    t = body + rng.normal(0, 0.07, shape)
    t += amp * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * spread ** 2))
    t[bg] = ambient + rng.normal(0, 0.1, bg.sum())
    buf = io.BytesIO()
    np.save(buf, t.astype(np.float32))
    return buf.getvalue()


def _rgb(rng, redness: float, blur: bool) -> bytes:
    w, h = 640, 480
    skin = np.array([205, 160, 135]) + rng.normal(0, 6, 3)
    arr = np.clip(skin + rng.normal(0, 9, (h, w, 3)), 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    d = ImageDraw.Draw(img)
    d.rectangle([300, 120, 330, 400], fill=(235, 235, 240))              # catheter / dressing
    r = int(20 + 60 * redness)
    if redness > 0.05:
        d.ellipse([315 - r, 260 - r, 315 + r, 260 + r], fill=(int(200 + 40 * redness), 110, 100))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def generate(session: Session, cfg: SynthConfig | None = None, who: Principal = SYSTEM_PRINCIPAL) -> dict:
    cfg = cfg or SynthConfig()
    rng = np.random.default_rng(cfg.seed)
    stats = {"patients": 0, "episodes": 0, "events": 0, "thermal_images": 0, "rgb_images": 0, "measurements": 0}
    for s in SITES:
        if not session.query(svc.Site).filter_by(site_code=s["site_code"]).first():
            svc.create_site(session, who, S.SiteIn(site_code=s["site_code"], name=s["name"],
                                                   timezone=s["timezone"], setting=s["setting"]))
    session.commit()
    screen_no = 0
    for i in range(cfg.n_patients):
        site = SITES[i % len(SITES)]
        # screening log: some non-eligible / declined candidates before each enrolment
        for _ in range(rng.poisson(0.5)):
            screen_no += 1
            reason, outcome, eligible = [("age < 18", "not_eligible", False),
                                         ("expected catheter dwell < 72 h", "not_eligible", False),
                                         ("declined consent", "declined", True)][rng.integers(3)]
            svc.create_screening(session, who, S.ScreeningIn(
                site_code=site["site_code"], screening_number=f"SCR{screen_no:05d}",
                screening_date=(cfg.start + timedelta(days=int(i * cfg.study_days / cfg.n_patients))).date(),
                eligible=eligible, outcome=outcome, exclusion_reason=reason))
        screen_no += 1
        insert = cfg.start + timedelta(days=float(i * cfg.study_days / cfg.n_patients), hours=float(rng.uniform(0, 10)))
        svc.create_screening(session, who, S.ScreeningIn(
            site_code=site["site_code"], screening_number=f"SCR{screen_no:05d}", screening_date=insert.date(),
            eligible=True, outcome="enrolled"))
        dialysis = site["setting"] == "dialysis"
        p = svc.create_patient(session, who, S.PatientIn(
            site_code=site["site_code"], screening_number=f"SCR{screen_no:05d}", enrolment_date=insert.date(),
            age_years=int(np.clip(rng.normal(63, 13), 19, 95)), sex=["female", "male"][rng.integers(2)],
            bmi=round(float(np.clip(rng.normal(27, 5), 16, 50)), 1), diabetes=bool(rng.random() < 0.35),
            chronic_kidney_disease=dialysis or bool(rng.random() < 0.2), on_dialysis=dialysis,
            immunosuppressed=bool(rng.random() < (0.5 if site["setting"] == "oncology" else 0.12)),
            consent_version="1.0", skin_phototype=["II", "III", "IV", "V"][rng.integers(4)]))
        stats["patients"] += 1
        n_eps = 2 if rng.random() < 0.08 else 1
        t_start = insert
        for _ in range(n_eps):
            _episode(session, who, rng, cfg, site, p, t_start, stats)
            t_start = t_start + timedelta(days=18)
        if i % 5 == 4:
            session.commit()
    session.commit()
    return stats


def _episode(session, who, rng, cfg: SynthConfig, site, p, insert: datetime, stats: dict) -> None:
    ctype = site["catheters"][rng.integers(len(site["catheters"]))]
    ins_site = INSERTION[ctype][rng.integers(len(INSERTION[ctype]))]
    dur_h = float(rng.uniform(5, 14) * 24)
    event = rng.random() < cfg.event_rate
    recog = None
    if event:
        recog_h = float(rng.uniform(48, dur_h - 12))
        recog = insert + timedelta(hours=recog_h)
        removal = recog + timedelta(hours=float(rng.uniform(12, 72)))
        end = removal
    else:
        removal = insert + timedelta(hours=dur_h) if rng.random() < 0.7 else None
        end = removal or insert + timedelta(hours=dur_h)
    ep = svc.create_episode(session, who, S.EpisodeIn(
        study_id=p.study_id, catheter_type=ctype, insertion_site=ins_site,
        tunnelled=ctype in ("PORT",) or (ctype == "HD_CVC" and rng.random() < 0.6), lumens=2,
        insertion_time=insert, removal_time=removal,
        removal_reason=("suspected infection" if event else "end of therapy") if removal else None,
        end_of_follow_up_time=None if removal else end,
        end_of_follow_up_reason=None if removal else "discharged with catheter in situ",
        imaging_interval_hours=cfg.imaging_interval_h))
    stats["episodes"] += 1
    stats["events"] += int(event)
    pre = cfg.preclinical_h * float(rng.uniform(0.7, 1.2))

    def local_inflammation(t: datetime) -> float:
        h_ins = (t - insert).total_seconds() / 3600
        amp = 0.7 * np.exp(-h_ins / 20)                       # post-insertion warmth
        if event:
            h_to = (recog - t).total_seconds() / 3600
            if h_to < pre:
                amp += cfg.hotspot_max_c * min(1.0, (pre - h_to) / pre)
        return float(amp)

    def systemic(t: datetime) -> float:
        if not event:
            return 0.0
        h_to = (recog - t).total_seconds() / 3600
        return float(np.clip((24 - h_to) / 36, 0, 1))

    # vitals / labs
    t = insert + timedelta(hours=1)
    base_hr = rng.normal(84, 10)
    while t < end:
        sy = systemic(t)
        vit = {"temperature_c": rng.normal(36.9 + 1.2 * sy, 0.3), "heart_rate": rng.normal(base_hr + 18 * sy, 6),
               "respiratory_rate": rng.normal(17 + 4 * sy, 2), "sbp": rng.normal(126 - 10 * sy, 10),
               "spo2": np.clip(rng.normal(96 - sy, 1.2), 85, 100)}
        vit["dbp"] = vit["sbp"] - rng.uniform(35, 55)
        for var, v in vit.items():
            if rng.random() < 0.06:
                continue   # realistic missingness (not recorded)
            if rng.random() < cfg.p_explicit_missing:
                m = S.MeasurementIn(study_id=p.study_id, episode_code=ep.episode_code, variable=var,
                                    missing_reason="ND", event_time=t, source_ref="SYNTHETIC")
            else:
                m = S.MeasurementIn(study_id=p.study_id, episode_code=ep.episode_code, variable=var,
                                    value=round(float(v), 1), event_time=t, source_ref="SYNTHETIC")
            svc.create_measurement(session, who, m)
            stats["measurements"] += 1
            if rng.random() < cfg.p_duplicate_observation:
                svc.create_measurement(session, who, m)   # duplicate entry, flagged by the DQ dashboard
                stats["duplicate_observations"] = stats.get("duplicate_observations", 0) + 1
        if (t - insert).total_seconds() / 3600 % 24 < cfg.vitals_interval_h:
            rep = t + timedelta(hours=float(rng.uniform(1.5, 6)))
            labs = {"wbc": rng.normal(8.5 + 6 * sy, 1.8), "crp": abs(rng.normal(25 + 120 * sy, 15)),
                    "platelets": rng.normal(230, 50), "creatinine": rng.normal(420 if site["setting"] == "dialysis"
                                                                               else 95, 20),
                    "haemoglobin": rng.normal(108, 12), "lactate": abs(rng.normal(1.3 + 1.0 * sy, 0.4))}
            for var, v in labs.items():
                if rng.random() < 0.08:
                    continue
                svc.create_measurement(session, who, S.MeasurementIn(
                    study_id=p.study_id, episode_code=ep.episode_code, variable=var, value=round(float(max(v, 0.1)), 2),
                    event_time=t, source_time=rep, source_ref="SYNTHETIC"))
                stats["measurements"] += 1
            # daily standard-of-care exit-site assessment (conventional comparator)
            infl = local_inflammation(t)
            recognised = event and t >= recog
            svc.create_assessment(session, who, S.AssessmentIn(
                study_id=p.study_id, episode_code=ep.episode_code, event_time=t, assessor_role="nurse",
                erythema_mm=round(max(0.0, rng.normal(4 + (6 * infl if recognised else 1.5 * infl), 2)), 0),
                induration_mm=round(max(0.0, rng.normal(1 + (4 * infl if recognised else 0), 1)), 0),
                tenderness=bool(recognised or rng.random() < 0.05), purulent_discharge=bool(recognised and
                                                                                            rng.random() < 0.6),
                clinical_impression="confirmed_infection" if recognised else (
                    "suspected_infection" if (event and (recog - t).total_seconds() < 6 * 3600) else "no_signs")))
        t += timedelta(hours=cfg.vitals_interval_h)

    # images
    cx = 80 + rng.integers(-15, 16)
    cy = 60 + rng.integers(-12, 13)
    base_skin = rng.normal(32.6, 0.5) + site["camera_offset"]
    t = insert + timedelta(minutes=float(rng.uniform(30, 120)))
    prev_bytes = None
    while t < end:
        if rng.random() < cfg.p_missed_image:
            t += timedelta(hours=cfg.imaging_interval_h)
            continue
        jitter = timedelta(minutes=float(rng.normal(0, 25)))
        ta = t + jitter
        amb = float(rng.normal(site["ambient"], 0.8))
        amp = local_inflammation(ta) + float(rng.normal(0, 0.25))
        data = _thermal(rng, cfg.image_shape, max(amp, 0), cx + rng.integers(-3, 4), cy + rng.integers(-3, 4),
                        base_skin + 0.01 * (amb - site["ambient"]), amb)
        fname = "img.npy"
        if rng.random() < cfg.p_corrupt:
            data, fname = data[: len(data) // 3], "img_truncated.npy"
        elif prev_bytes is not None and rng.random() < cfg.p_duplicate:
            data = prev_bytes
        meta = {"episode_id": ep.id, "acquisition_time": ta, "view": "frontal",
            "distance_cm": float(rng.normal(40, 3)), "ambient_temperature_c": round(amb, 1),
            "relative_humidity_pct": float(rng.normal(45, 8)), "emissivity": 0.98, "camera_make": "SYNTHETIC",
            "camera_model": "SIM-160", "device_id": f"{site['site_code']}-CAM1",
            "operator_id": f"{site['site_code']}-OP{rng.integers(1, 4)}"}
        res = ingest_image(session, who, "thermal", data, fname, meta, channel="upload")
        stats["thermal_images"] += 1
        prev_bytes = data
        res.image.extra_metadata = {"synthetic": True, "synthetic_insertion_point": {"x": int(cx), "y": int(cy)}}
        if cfg.rgb:
            redness = min(1.0, max(0.0, (local_inflammation(ta) - 0.8) / 2.5)) if event else 0.0
            ingest_image(session, who, "rgb", _rgb(rng, redness, rng.random() < cfg.p_blur), "img.jpg",
                         {**meta, "acquisition_time": ta + timedelta(seconds=5)}, channel="upload")
            stats["rgb_images"] += 1
        t += timedelta(hours=cfg.imaging_interval_h)

    if event:
        svc.create_medication(session, who, S.MedicationIn(
            study_id=p.study_id, episode_code=ep.episode_code, drug_name="vancomycin", atc_code="J01XA01",
            route="iv", dose=1000, dose_unit="mg", frequency="q12h", indication="empirical",
            start_time=recog + timedelta(hours=2), end_time=recog + timedelta(days=10)))
        svc.create_microbiology(session, who, S.MicrobiologyIn(
            study_id=p.study_id, episode_code=ep.episode_code, specimen_type="exit_site_swab",
            event_time=recog + timedelta(hours=1), source_time=recog + timedelta(hours=40), growth="positive",
            organism=ORGANISMS[rng.integers(len(ORGANISMS))]))
        bsi = rng.random() < 0.25
        svc.create_microbiology(session, who, S.MicrobiologyIn(
            study_id=p.study_id, episode_code=ep.episode_code, specimen_type="blood_peripheral",
            event_time=recog + timedelta(hours=1), source_time=recog + timedelta(hours=48),
            growth="positive" if bsi else "negative", organism="Staphylococcus aureus" if bsi else None))
        otype = "exit_site_infection" if rng.random() < 0.8 else ("tunnel_infection" if ctype != "PORT"
                                                                   else "pocket_infection")
        svc.create_outcome(session, who, S.OutcomeIn(
            study_id=p.study_id, episode_code=ep.episode_code, outcome_type=otype,
            clinical_recognition_time=recog, onset_time_reference=recog - timedelta(hours=pre * 0.6),
            treatment_start_time=recog + timedelta(hours=2), status="adjudicated" if rng.random() < 0.85
            else "provisional", criteria_version="IRT-CRI-DEF-1.0", catheter_removed_due_to_infection=True))
    else:
        svc.create_outcome(session, who, S.OutcomeIn(
            study_id=p.study_id, episode_code=ep.episode_code, outcome_type="no_infection",
            status="adjudicated", criteria_version="IRT-CRI-DEF-1.0"))
    if rng.random() < 0.1:
        svc.create_deviation(session, who, S.DeviationIn(
            site_code=site["site_code"], study_id=p.study_id, episode_code=ep.episode_code,
            category="missed_imaging", severity="minor", description="SYNTHETIC: imaging timepoint missed",
            event_time=insert + timedelta(hours=36)))
