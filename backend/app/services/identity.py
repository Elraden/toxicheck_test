import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.identity import SessionUser


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Principal:
    session_id: str
    user: SessionUser
    csrf: str
    kind: str


class IdentityService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def read(self, token: str | None, kind: str) -> Principal | None:
        if not token or len(token) != 43 or not all(c.isalnum() or c in '_-' for c in token):
            return None
        row = (await self.db.execute(text("""
            SELECT s.id AS session_id, s.csrf_token, u.id, u.role, u.account_kind, u.display_name
            FROM public.user_sessions s JOIN public.users u ON u.id=s.user_id
            WHERE s.token_hash=:hash AND s.kind=:kind AND s.revoked_at IS NULL
              AND s.expires_at > now() AND u.is_active
              AND (:kind != 'admin' OR (u.role='admin' AND u.account_kind='account'
                   AND s.last_seen_at > now() - make_interval(mins => :idle)))
              AND (:kind != 'guest' OR (u.role='user' AND u.account_kind='guest'))
        """), {"hash": token_hash(token), "kind": kind, "idle": settings.admin_idle_minutes})).mappings().first()
        if row is None:
            return None
        await self.db.execute(text("""
            UPDATE public.user_sessions SET last_seen_at=now()
            WHERE id=:id AND last_seen_at < now() - interval '1 minute'
        """), {"id": row["session_id"]})
        return Principal(str(row["session_id"]), SessionUser(**row), row["csrf_token"], kind)

    async def issue(self, user: SessionUser, kind: str) -> tuple[str, Principal]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        lifetime = timedelta(hours=settings.admin_session_hours) if kind == "admin" else timedelta(days=settings.guest_session_days)
        session_id = await self.db.scalar(text("""
            INSERT INTO public.user_sessions(user_id, token_hash, csrf_token, kind, expires_at)
            VALUES (:user, :hash, :csrf, :kind, :expires) RETURNING id
        """), {"user": user.id, "hash": token_hash(token), "csrf": csrf, "kind": kind,
               "expires": datetime.now(timezone.utc) + lifetime})
        return token, Principal(str(session_id), user, csrf, kind)

    async def create_guest(self) -> tuple[str, Principal]:
        row = (await self.db.execute(text("""
            INSERT INTO public.users(role, account_kind) VALUES ('user', 'guest')
            RETURNING id, role, account_kind, display_name
        """))).mappings().one()
        user = SessionUser(**row)
        await self.db.execute(text("INSERT INTO public.user_preferences(user_id) VALUES (:id)"), {"id": user.id})
        return await self.issue(user, "guest")

    async def revoke(self, principal: Principal, all_sessions: bool = False):
        await self.db.execute(text("""
            UPDATE public.user_sessions SET revoked_at=now()
            WHERE (id=:session OR (:all AND user_id=:user AND kind=:kind)) AND revoked_at IS NULL
        """), {"session": principal.session_id, "all": all_sessions, "user": principal.user.id, "kind": principal.kind})
