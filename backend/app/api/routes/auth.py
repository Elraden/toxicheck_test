import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.schemas.auth import AuthFeatureState
from app.schemas.identity import AdminLogin, BrowserSession, SessionUser
from app.api.identity_dependencies import admin_write, cookie_name, guest_write, require_browser_origin, throttle
from app.core.config import settings
from app.db.session import get_identity_session
from app.services.identity import IdentityService, Principal
from app.services.passwords import hasher, verify_password

router = APIRouter()
password_slots = asyncio.Semaphore(2)


def set_session_cookie(response: Response, token: str, kind: str):
    age = settings.admin_session_hours * 3600 if kind == "admin" else settings.guest_session_days * 86400
    response.set_cookie(cookie_name(kind), token, max_age=age, secure=settings.secure_cookies,
                        httponly=True, samesite="lax", path="/")


async def browser_state(request: Request, service: IdentityService) -> BrowserSession:
    guest = await service.read(request.cookies.get(cookie_name("guest")), "guest")
    admin = await service.read(request.cookies.get(cookie_name("admin")), "admin")
    return BrowserSession(user=guest.user if guest else None, csrf=guest.csrf if guest else None,
                          admin=admin.user if admin else None, admin_csrf=admin.csrf if admin else None)


@router.get("/state", response_model=AuthFeatureState)
async def auth_state() -> AuthFeatureState:
    return AuthFeatureState(status="guest_and_admin", message="Guest profiles and administrator login are available.")


@router.get("/session", response_model=BrowserSession)
async def session_state(request: Request, db: AsyncSession = Depends(get_identity_session)):
    return await browser_state(request, IdentityService(db))


@router.post("/guest", response_model=BrowserSession, dependencies=[Depends(require_browser_origin)])
async def create_guest(request: Request, response: Response, db: AsyncSession = Depends(get_identity_session)):
    service = IdentityService(db)
    state = await browser_state(request, service)
    if state.user:
        return state
    await throttle("guest:" + (request.client.host if request.client else "unknown"), 60, 3600)
    token, principal = await service.create_guest()
    set_session_cookie(response, token, "guest")
    state.user, state.csrf = principal.user, principal.csrf
    return state


@router.post("/admin/login", response_model=BrowserSession)
async def admin_login(payload: AdminLogin, request: Request, response: Response,
                      guest: Principal = Depends(guest_write), db: AsyncSession = Depends(get_identity_session)):
    login = payload.login.lower()
    await throttle("login-ip:" + (request.client.host if request.client else "unknown"), 20, 60)
    await throttle("login-account:" + login, 5, 900)
    row = (await db.execute(text("SELECT * FROM public.users WHERE lower(login)=:login"), {"login": login})).mappings().first()
    async with password_slots:
        valid = await run_in_threadpool(verify_password, row["password_hash"] if row else None, payload.password)
    if not valid:
        raise HTTPException(401, "Неверный логин или пароль")
    # Recheck under lock: password reset, disable or role revocation must win over an in-flight login.
    current = (await db.execute(text("""
        SELECT * FROM public.users WHERE id=:id AND password_hash=:hash
          AND role='admin' AND account_kind='account' AND is_active FOR UPDATE
    """), {"id": row["id"], "hash": row["password_hash"]})).mappings().first()
    if current is None:
        raise HTTPException(401, "Неверный логин или пароль")
    if hasher.check_needs_rehash(current["password_hash"]):
        encoded = await run_in_threadpool(hasher.hash, payload.password)
        await db.execute(text("UPDATE public.users SET password_hash=:hash, updated_at=now() WHERE id=:id"),
                         {"hash": encoded, "id": current["id"]})
    service = IdentityService(db)
    old = await service.read(request.cookies.get(cookie_name("admin")), "admin")
    if old:
        await service.revoke(old)
    token, admin = await service.issue(SessionUser(**current), "admin")
    set_session_cookie(response, token, "admin")
    return BrowserSession(user=guest.user, csrf=guest.csrf, admin=admin.user, admin_csrf=admin.csrf)


@router.post("/admin/logout", response_model=BrowserSession)
async def admin_logout(request: Request, response: Response, all_sessions: bool = False,
                       admin: Principal = Depends(admin_write), db: AsyncSession = Depends(get_identity_session)):
    service = IdentityService(db)
    await service.revoke(admin, all_sessions)
    response.delete_cookie(cookie_name("admin"), path="/", secure=settings.secure_cookies, httponly=True, samesite="lax")
    return await browser_state(request, service)

