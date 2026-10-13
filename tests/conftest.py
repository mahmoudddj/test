"""Shared fixtures: isolated SQLite DB + local object store per test session/function."""

from __future__ import annotations

import io
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

os.environ.setdefault("IRTCRI_ENV", "test")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    # Set IRTCRI_TEST_DATABASE_URL (e.g. postgresql+psycopg://...) to run the suite against PostgreSQL.
    db_url = os.environ.get("IRTCRI_TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/test.db"
    if db_url.startswith("postgresql"):
        from irtcri.db import models  # noqa: F401
        from irtcri.db.base import Base
        from irtcri.db.session import get_engine
        Base.metadata.drop_all(get_engine(db_url))
    monkeypatch.setenv("IRTCRI_DATABASE_URL", db_url)
    monkeypatch.setenv("IRTCRI_LOCAL_STORE_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("IRTCRI_BOOTSTRAP_ADMIN_TOKEN", "bootstrap-secret-for-tests")
    from irtcri.core.config import reset_settings_cache
    reset_settings_cache()
    from irtcri.imaging.storage import set_object_store
    set_object_store(None)
    from irtcri.db.session import create_all
    create_all(db_url)
    yield {"db_url": db_url, "tmp": tmp_path}
    set_object_store(None)
    reset_settings_cache()


@pytest.fixture()
def session(env):
    from irtcri.db.session import session_factory
    s = session_factory(env["db_url"])()
    yield s
    s.close()


@pytest.fixture()
def admin():
    from irtcri.core.security import SYSTEM_PRINCIPAL
    return SYSTEM_PRINCIPAL


T0 = datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc)


@pytest.fixture()
def seeded(session, admin):
    """One site, one patient, one episode."""
    from irtcri.hub import schemas as S
    from irtcri.hub import services as svc
    svc.create_site(session, admin, S.SiteIn(site_code="S01", name="Site One", timezone="Europe/Berlin"))
    p = svc.create_patient(session, admin, S.PatientIn(site_code="S01", enrolment_date=T0.date(), age_years=64,
                                                       sex="female", diabetes=True))
    ep = svc.create_episode(session, admin, S.EpisodeIn(study_id=p.study_id, catheter_type="HD_CVC",
                                                        insertion_site="internal_jugular_right", tunnelled=True,
                                                        insertion_time=T0, imaging_interval_hours=12))
    session.commit()
    return {"patient": p, "episode": ep}


def thermal_npy(hot: float = 0.0, seed: int = 0, shape=(120, 160)) -> bytes:
    rng = np.random.default_rng(seed)
    h, w = shape
    yy, xx = np.mgrid[:h, :w]
    t = 32.0 + 0.6 * np.sin(xx / 25) + rng.normal(0, 0.08, shape)
    t += hot * np.exp(-((yy - h / 2) ** 2 + (xx - w / 2) ** 2) / (2 * 12 ** 2))
    buf = io.BytesIO()
    np.save(buf, t.astype(np.float32))
    return buf.getvalue()


def rgb_jpeg(seed: int = 0, blur: bool = False) -> bytes:
    from PIL import Image, ImageFilter
    rng = np.random.default_rng(seed)
    arr = (rng.random((480, 640, 3)) * 80 + 100).astype(np.uint8)
    arr[150:330, 220:420] = [40, 30, 30]
    img = Image.fromarray(arr)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(12))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


@pytest.fixture()
def t0():
    return T0


@pytest.fixture()
def hours():
    return lambda h: T0 + timedelta(hours=h)
