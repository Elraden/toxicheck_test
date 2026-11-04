import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import AnnotationSessionLocal, get_identity_session
from app.services.identity import IdentityService, Principal, token_hash


def cookie_name(kind: str) -> str:
    return ("__Host-" if settings.secure_cookies else "") + f"toxicheck_{kind}"


def require_browser_origin(request: Request):
    if (request.headers.get("origin") not in settings.frontend_origins
            or request.headers.get("x-toxicheck-request") != "1"):
        raise HTTPException(403, "Недопустимый источник запроса")


def check_csrf(request: Request, principal: Principal):
    require_browser_origin(request)
    supplied = request.headers.get("x-csrf-token", "")
    if not secrets.compare_digest(supplied, principal.csrf):
        raise HTTPException(403, "Сессия изменилась. Обновите страницу.")


async def guest_user(request: Request, db: AsyncSession = Depends(get_identity_session)) -> Principal:
    principal = await IdentityService(db).read(request.cookies.get(cookie_name("guest")), "guest")
    if principal is None:
        raise HTTPException(401, "Гостевая сессия истекла. Обновите страницу.")
    return principal


async def guest_write(request: Request, principal: Principal = Depends(guest_user)) -> Principal:
    check_csrf(request, principal)
    return principal


async def require_admin(request: Request, db: AsyncSession = Depends(get_identity_session)) -> Principal:
    principal = await IdentityService(db).read(request.cookies.get(cookie_name("admin")), "admin")
    if principal is None:
        raise HTTPException(403, "Требуется вход администратора")
    return principal


async def admin_write(request: Request, principal: Principal = Depends(require_admin)) -> Principal:
    check_csrf(request, principal)
    return principal


async def throttle(key: str, limit: int, seconds: int):
    # Independent transaction: unsuccessful login must not roll back its attempt counter.
    async with AnnotationSessionLocal() as db, db.begin():
        attempts = await db.scalar(text("""
            INSERT INTO public.auth_rate_limits(key, attempts, reset_at)
            VALUES (:key, 1, now() + make_interval(secs => :seconds))
            ON CONFLICT (key) DO UPDATE SET
                attempts=CASE WHEN auth_rate_limits.reset_at <= now() THEN 1 ELSE auth_rate_limits.attempts+1 END,
                reset_at=CASE WHEN auth_rate_limits.reset_at <= now() THEN EXCLUDED.reset_at ELSE auth_rate_limits.reset_at END
            RETURNING attempts
        """), {"key": token_hash(key), "seconds": seconds})
        await db.execute(text("DELETE FROM public.auth_rate_limits WHERE reset_at < now() - interval '1 day'"))
    if attempts > limit:
        raise HTTPException(429, "Слишком много попыток. Попробуйте позже.", headers={"Retry-After": str(seconds)})
