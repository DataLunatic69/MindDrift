from fastapi import APIRouter

from app.api.endpoint.v1.auth import router as auth_router
from app.api.endpoint.v1.canvas import router as canvas_router
from app.api.endpoint.v1.collisions import router as collisions_router
from app.api.endpoint.v1.drifts import router as drifts_router
from app.api.endpoint.v1.fragments import router as fragments_router
from app.api.endpoint.v1.lenses import router as lenses_router
from app.api.endpoint.v1.memory import router as memory_router
from app.api.endpoint.v1.search import router as search_router
from app.api.endpoint.v1.syntheses import (
    drifts_router as synth_drifts_router,
    syntheses_router,
)
from app.api.endpoint.v1.ws import router as ws_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router)
api_router.include_router(fragments_router)
api_router.include_router(lenses_router)
api_router.include_router(collisions_router)
api_router.include_router(canvas_router)
api_router.include_router(search_router)
api_router.include_router(drifts_router)
# /drifts/{id}/synthesize lives on the same prefix — mount on the same path.
api_router.include_router(synth_drifts_router)
api_router.include_router(syntheses_router)
api_router.include_router(memory_router)
api_router.include_router(ws_router)
