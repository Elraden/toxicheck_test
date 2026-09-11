from fastapi import APIRouter

from app.api.routes.analysis import router as analysis_router
from app.api.routes.auth import router as auth_router
from app.api.routes.billing import router as billing_router
from app.api.routes.compare import router as compare_router
from app.api.routes.health import router as health_router
from app.api.routes.ingredients import router as ingredients_router
from app.api.routes.preferences import router as preferences_router
from app.api.routes.scan import router as scan_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(analysis_router, prefix="/analysis", tags=["analysis"])
api_router.include_router(scan_router, prefix="/scan", tags=["scan"])
api_router.include_router(ingredients_router, prefix="/ingredients", tags=["ingredients"])
api_router.include_router(preferences_router, prefix="/preferences", tags=["preferences"])
api_router.include_router(compare_router, prefix="/compare", tags=["compare"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(billing_router, prefix="/billing", tags=["billing"])
