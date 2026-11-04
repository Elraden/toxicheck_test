import secrets
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.identity_dependencies import cookie_name, guest_write, throttle
from app.core.config import settings
from app.db.session import get_identity_session
from app.main import create_app
from app.schemas.identity import SessionUser
from app.services.identity import IdentityService, Principal, token_hash
from app.services.passwords import hasher, verify_password

ORIGIN = "http://localhost:5173"
HEADERS = {"Origin": ORIGIN, "X-ToxiCheck-Request": "1"}


def principal(kind="guest"):
    return Principal(str(uuid4()), SessionUser(id=uuid4(), role="admin" if kind == "admin" else "user",
        account_kind="account" if kind == "admin" else "guest"), secrets.token_urlsafe(32), kind)


class IdentityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.db = AsyncMock()
        async def db():
            yield self.db
        self.app.dependency_overrides[get_identity_session] = db

    def test_guest_cookie_flags_and_no_token_in_json(self):
        guest = principal()
        token = secrets.token_urlsafe(32)
        with patch.object(settings, "app_env", "production"), \
             patch.object(IdentityService, "read", new_callable=AsyncMock, return_value=None), \
             patch.object(IdentityService, "create_guest", new_callable=AsyncMock, return_value=(token, guest)) as create, \
             patch("app.api.routes.auth.throttle", new_callable=AsyncMock), \
             TestClient(self.app, base_url="https://testserver") as client:
            self.assertEqual(client.post("/api/auth/guest", json={}).status_code, 403)
            self.assertEqual(client.post("/api/auth/guest", json={}, headers={**HEADERS, "Origin": "https://evil.example"}).status_code, 403)
            create.assert_not_awaited()
            response = client.post("/api/auth/guest", json={}, headers=HEADERS)
            self.assertEqual(response.status_code, 200, response.text)
            cookie = response.headers["set-cookie"]
            for attribute in ("__Host-toxicheck_guest=", "HttpOnly", "Secure", "SameSite=lax", "Path=/"):
                self.assertIn(attribute, cookie)
            self.assertNotIn("Domain=", cookie)
            self.assertNotIn(token, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json()["user"]["role"], "user")

    def test_missing_session_cannot_read_preferences_or_admin(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/api/preferences/me").status_code, 401)
            self.assertEqual(client.get("/api/admin/me").status_code, 403)

    def test_write_requires_csrf_origin_and_does_not_accept_user_id(self):
        guest = principal()
        with patch.object(IdentityService, "read", new_callable=AsyncMock, return_value=guest), \
             patch("app.api.routes.preferences.PersonalPreferenceService.save", new_callable=AsyncMock) as save, \
             TestClient(self.app) as client:
            data = {"excludedIngredientIds": [], "revision": 0}
            for headers in ({}, HEADERS, {**HEADERS, "X-CSRF-Token": "wrong"},
                            {**HEADERS, "X-CSRF-Token": guest.csrf, "Origin": "null"}):
                self.assertEqual(client.put("/api/preferences/me", json=data, headers=headers).status_code, 403)
            save.assert_not_awaited()
            response = client.put("/api/preferences/me", json={**data, "user_id": str(uuid4())},
                                  headers={**HEADERS, "X-CSRF-Token": guest.csrf})
            self.assertEqual(response.status_code, 422)

    def test_login_rotates_admin_session_and_never_trusts_role_payload(self):
        guest, admin, previous = principal(), principal("admin"), principal("admin")
        row = {**admin.user.model_dump(), "password_hash": hasher.hash("test password long enough"), "is_active": True}
        result = MagicMock()
        result.mappings.return_value.first.return_value = row
        self.db.execute.return_value = result
        async def read(token, kind):
            return guest if kind == "guest" else previous
        with patch.object(IdentityService, "read", side_effect=read), \
             patch.object(IdentityService, "issue", new_callable=AsyncMock, return_value=(secrets.token_urlsafe(32), admin)), \
             patch.object(IdentityService, "revoke", new_callable=AsyncMock) as revoke, \
             patch("app.api.routes.auth.throttle", new_callable=AsyncMock) as limited, \
             TestClient(self.app) as client:
            headers = {**HEADERS, "X-CSRF-Token": guest.csrf}
            data = {"login": "admin", "password": "test password long enough"}
            self.assertEqual(client.post("/api/auth/admin/login", json={**data, "role": "admin"}, headers=headers).status_code, 422)
            response = client.post("/api/auth/admin/login", json=data, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["admin"]["role"], "admin")
            self.assertEqual(response.json()["user"]["id"], str(guest.user.id))
            revoke.assert_awaited_once_with(previous)
            self.assertEqual(limited.await_count, 2)
            self.assertIn(cookie_name("admin"), response.headers["set-cookie"])

    def test_wrong_and_nonexistent_login_have_same_error(self):
        guest = principal()
        result = MagicMock()
        self.db.execute.return_value = result
        with patch.object(IdentityService, "read", new_callable=AsyncMock, return_value=guest), \
             patch("app.api.routes.auth.throttle", new_callable=AsyncMock), TestClient(self.app) as client:
            responses = []
            for row in (None, {"password_hash": hasher.hash("different password long enough")}):
                result.mappings.return_value.first.return_value = row
                response = client.post("/api/auth/admin/login", json={"login": "admin", "password": "wrong"},
                                       headers={**HEADERS, "X-CSRF-Token": guest.csrf})
                self.assertEqual(response.status_code, 401)
                responses.append(response.json())
            self.assertEqual(*responses)


class IdentityStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_random_tokens_are_stored_only_as_hashes(self):
        db = AsyncMock()
        db.scalar.return_value = uuid4()
        token, session = await IdentityService(db).issue(principal().user, "guest")
        self.assertEqual(len(token), 43)
        params = db.scalar.await_args.args[1]
        self.assertEqual(params["hash"], token_hash(token))
        self.assertNotIn(token, params.values())
        self.assertNotEqual(session.csrf, token)

    async def test_lookup_checks_expiry_revocation_role_and_idle_timeout(self):
        db = AsyncMock()
        result = MagicMock()
        result.mappings.return_value.first.return_value = None
        db.execute.return_value = result
        self.assertIsNone(await IdentityService(db).read(secrets.token_urlsafe(32), "admin"))
        sql = str(db.execute.await_args.args[0])
        for condition in ("s.expires_at > now()", "s.revoked_at IS NULL", "u.is_active", "u.role='admin'", "s.last_seen_at >"):
            self.assertIn(condition, sql)

    async def test_rate_limit_is_committed_before_rejection(self):
        db = MagicMock()
        db.begin.return_value = AsyncMock()
        db.scalar = AsyncMock(return_value=6)
        db.execute = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = db
        with patch("app.api.identity_dependencies.AnnotationSessionLocal", return_value=context):
            with self.assertRaises(HTTPException) as error:
                await throttle("login:test", 5, 900)
        self.assertEqual(error.exception.status_code, 429)
        db.begin.return_value.__aexit__.assert_awaited_once_with(None, None, None)


class PasswordTests(unittest.TestCase):
    def test_salted_password_hash_and_invalid_hash(self):
        first, second = hasher.hash("long test password"), hasher.hash("long test password")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password(first, "long test password"))
        self.assertFalse(verify_password(first, "wrong"))
        self.assertFalse(verify_password(None, "wrong"))
        self.assertFalse(verify_password("invalid", "wrong"))
