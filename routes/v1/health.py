"""
Health check routes — v1.

Endpoints:
  GET /health            — root-level liveness probe (Docker / load balancer)
  GET /api/v1/health     — versioned health check
"""
from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Liveness probe")
@router.get("/api/v1/health", summary="Versioned health check")
async def health():
    return {"status": "ok", "version": "v1"}
