from fastapi import APIRouter

from app.api.endpoint.v1.canvas import router as canvas_router
from app.api.endpoint.v1.collisions import router as collisions_router
from app.api.endpoint.v1.fragments import router as fragments_router
from app.api.endpoint.v1.search import router as search_router
from app.api.endpoint.v1.ws import router as ws_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(fragments_router)
api_router.include_router(collisions_router)
api_router.include_router(canvas_router)
api_router.include_router(search_router)
api_router.include_router(ws_router)
