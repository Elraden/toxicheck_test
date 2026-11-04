from collections import OrderedDict
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_annotation_session
from app.schemas.corrections import SaveCorrectionsRequest, SaveCorrectionsResponse
from app.services.correction_service import CorrectionService
from app.api.identity_dependencies import guest_write
from app.services.identity import Principal

router = APIRouter()
_windows: OrderedDict[str, tuple[float, int]] = OrderedDict()


async def limit_corrections(request: Request) -> None:
    # Bounded per-process guard for the anonymous endpoint; no addresses stored in DB.
    key = request.client.host if request.client else "unknown"
    now = monotonic()
    start, count = _windows.pop(key, (now, 0))
    if now - start >= 60:
        start, count = now, 0
    _windows[key] = (start, count + 1)
    if len(_windows) > 2048:
        _windows.popitem(last=False)
    if count >= 30:
        raise HTTPException(429, "Слишком много исправлений. Повторите через минуту.",
                            headers={"Retry-After": "60"})


@router.post("/corrections", response_model=SaveCorrectionsResponse,
             dependencies=[Depends(limit_corrections)])
async def save_corrections(payload: SaveCorrectionsRequest,
                           principal: Principal = Depends(guest_write),
                           session: AsyncSession = Depends(get_annotation_session)):
    return await CorrectionService(session).save(payload, user_id=principal.user.id)
