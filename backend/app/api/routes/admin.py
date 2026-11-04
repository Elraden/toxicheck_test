from fastapi import APIRouter, Depends

from app.api.identity_dependencies import require_admin
from app.schemas.identity import SessionUser
from app.services.identity import Principal

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/me", response_model=SessionUser)
async def admin_profile(principal: Principal = Depends(require_admin)):
    return principal.user
