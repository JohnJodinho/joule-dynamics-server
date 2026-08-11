"""
Routes package — exports all versioned API routers.
Mount order matters: more specific prefixes first.
"""
from routes.v1 import router as v1_router

__all__ = ["v1_router"]
