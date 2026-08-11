"""
v1 router — aggregates all v1 sub-routers into a single mountable router.
"""
from fastapi import APIRouter

from routes.v1.real_estate import router as real_estate_router
from routes.v1.rag import router as rag_router
from routes.v1.health import router as health_router

router = APIRouter()
router.include_router(real_estate_router)
router.include_router(rag_router)
router.include_router(health_router)
