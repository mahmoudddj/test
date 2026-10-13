"""IRT-CRI Data Hub API (FastAPI application factory)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from irtcri import INVESTIGATIONAL_DISCLAIMER, __version__
from irtcri.core.config import get_settings
from irtcri.hub.services import ServiceError, ValidationFailed
from irtcri.imaging.ingest import IngestError


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="IRT-CRI Research Data Hub",
        version=__version__,
        description="Research data platform of the IRT-in-CRI Study. " + INVESTIGATIONAL_DISCLAIMER,
    )
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type", "X-Request-ID"])

    @app.exception_handler(ServiceError)
    async def _svc(_: Request, exc: ServiceError):
        body = {"detail": str(exc)}
        if isinstance(exc, ValidationFailed):
            body["errors"] = exc.errors
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(IngestError)
    async def _ing(_: Request, exc: IngestError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(PermissionError)
    async def _perm(_: Request, exc: PermissionError):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    from irtcri.core.logging import configure_logging, request_logger
    configure_logging()
    app.middleware("http")(request_logger())

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    from irtcri.hub.routers import admin, annotation, clinical, dashboard, enrolment, exports, images, imports, models
    for r in (admin, enrolment, clinical, imports, images, annotation, models, dashboard, exports):
        app.include_router(r.router, prefix="/api/v1")

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok", "version": __version__, "env": settings.env}

    @app.get("/health/ready", tags=["meta"], summary="Dependency readiness (no data, no auth)")
    def ready():
        """ok/fail per dependency, used by scripts/verify_deployment.sh and orchestrators."""
        from fastapi.responses import JSONResponse
        from sqlalchemy import text

        from irtcri.db.session import get_engine
        from irtcri.imaging.repository import get_xnat_repository
        from irtcri.imaging.storage import get_object_store, raw_bucket
        checks = {}
        try:
            with get_engine().connect() as c:
                c.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"fail: {type(e).__name__}"
        try:
            get_object_store().list(raw_bucket(), "__readiness_probe__/")
            checks["object_store"] = "ok"
        except Exception as e:
            checks["object_store"] = f"fail: {type(e).__name__}"
        if settings.xnat_enabled:
            try:
                get_xnat_repository().check_project()
                checks["xnat"] = "ok"
            except Exception as e:
                checks["xnat"] = f"fail: {type(e).__name__}"
        else:
            checks["xnat"] = "disabled"
        ok = all(v in ("ok", "disabled") for v in checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={"ready": ok, "checks": checks})

    @app.get("/api/v1/meta", tags=["meta"])
    def meta():
        from irtcri.schemas.dictionary import load_dictionary
        return {"version": __version__, "disclaimer": INVESTIGATIONAL_DISCLAIMER,
                "data_dictionary_version": load_dictionary().version,
                "prospective_alerts_enabled": settings.prospective_alerts_enabled}

    return app


app = create_app()
