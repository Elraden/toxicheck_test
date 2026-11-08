from fastapi import APIRouter

from app.api.routes.analysis import router as analysis_router
from app.api.routes.auth import router as auth_router
from app.api.routes.billing import router as billing_router
from app.api.routes.compare import router as compare_router
from app.api.routes.health import router as health_router
from app.api.routes.ingredients import router as ingredients_router
from app.api.routes.preferences import router as preferences_router
from app.api.routes.scan import router as scan_router
from app.api.routes.corrections import router as corrections_router
from app.api.routes.admin import router as admin_router
from app.api.routes.research import router as research_router
from app.api.routes.research_admin import router as research_admin_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(analysis_router, prefix="/analysis", tags=["analysis"])
api_router.include_router(scan_router, prefix="/scan", tags=["scan"])
api_router.include_router(corrections_router, prefix="/scan", tags=["scan"])
api_router.include_router(ingredients_router, prefix="/ingredients", tags=["ingredients"])
api_router.include_router(preferences_router, prefix="/preferences", tags=["preferences"])
api_router.include_router(compare_router, prefix="/compare", tags=["compare"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(admin_router, prefix="/admin", tags=["admin"])
api_router.include_router(research_router, prefix="/research", tags=["research"])
api_router.include_router(research_admin_router, prefix="/admin/research", tags=["research-admin"])
api_router.include_router(billing_router, prefix="/billing", tags=["billing"])

from app.api.routes.orchestration import router as orchestration_router
api_router.include_router(orchestration_router, prefix='/admin/orchestration', tags=['orchestration'])
