from fastapi import APIRouter

from app.api.v1 import (
    academy, analysis, auth, external, ingest, matches, meta, simulation, squad,
    transfers,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(meta.router)
api_router.include_router(squad.router)
api_router.include_router(matches.router)
api_router.include_router(ingest.router)
api_router.include_router(external.router)
api_router.include_router(analysis.router)
api_router.include_router(simulation.router)
api_router.include_router(transfers.router)
api_router.include_router(academy.router)

__all__ = ["api_router"]
