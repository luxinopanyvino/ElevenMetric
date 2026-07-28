"""ElevenMetric API entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.core.config import settings
from app.core.tenancy import CrossTenantAccess
from app.db.session import init_db

logger = logging.getLogger("elevenmetric")

DESCRIPTION = """
Multi-tenant football analysis platform.

**What it does.** Takes what a club actually has — a teamsheet, an event feed,
tracking data, or raw video — and returns tactical findings and concrete
proposals: substitutions with timing, formation changes, transfer targets inside
a real budget, and academy promotion timelines.

**Input tiers.** Capabilities scale with the data you supply. See
`GET /api/v1/meta/data-requirements` for the full contract and
`GET /api/v1/meta/data-readiness` for a score of your own club's data.

**Isolation.** Every business table is tenant-scoped and every query is built
through a tenant-bound scope, so one club can never read another's data.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.services.cv import pipeline as cv_pipeline

    caps = cv_pipeline.capabilities()
    logger.info("CV engine: %s — %s", caps["engine"], caps["note"])
    yield


app = FastAPI(
    title="ElevenMetric",
    description=DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(CrossTenantAccess)
async def _cross_tenant_handler(request: Request, exc: CrossTenantAccess) -> JSONResponse:
    """Never confirm that another tenant's object exists — 404, not 403."""
    logger.warning("Cross-tenant access blocked on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=404, content={"detail": "Not found"})


@app.get("/health", tags=["meta"])
def health() -> dict:
    from app.services.cv import pipeline as cv_pipeline

    return {
        "status": "ok",
        "version": app.version,
        "cv_engine": cv_pipeline.capabilities()["engine"],
    }


app.include_router(api_router, prefix=settings.api_v1_prefix)

# The frontend is plain static files; serving them from the API keeps the demo
# to a single process and avoids CORS in development.
_frontend = Path(__file__).resolve().parents[2] / "frontend"
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
