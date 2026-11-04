from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.identity import PersonalPreferences


class PersonalPreferenceService:
    def __init__(self, db: AsyncSession, user_id: UUID):
        self.db, self.user_id = db, user_id

    async def read(self) -> PersonalPreferences:
        row = (await self.db.execute(text("""
            SELECT revision, local_imported_at FROM public.user_preferences WHERE user_id=:user
        """), {"user": self.user_id})).mappings().first()
        ids = (await self.db.scalars(text("""
            SELECT ingredient_id::text FROM public.user_ingredient_exclusions WHERE user_id=:user ORDER BY ingredient_id
        """), {"user": self.user_id})).all()
        return PersonalPreferences(excludedIngredientIds=list(ids), revision=row["revision"] if row else 0,
                                   localImported=bool(row and row["local_imported_at"]))

    async def lock(self):
        await self.db.execute(text("INSERT INTO public.user_preferences(user_id) VALUES (:user) ON CONFLICT DO NOTHING"), {"user": self.user_id})
        return (await self.db.execute(text("SELECT * FROM public.user_preferences WHERE user_id=:user FOR UPDATE"),
                                      {"user": self.user_id})).mappings().one()

    async def save(self, ids: list[UUID], revision: int) -> PersonalPreferences:
        row = await self.lock()
        if row["revision"] != revision:
            raise HTTPException(409, "Исключения изменились в другой вкладке. Загрузите актуальные настройки.")
        desired = {str(value) for value in ids}
        valid = set((await self.db.scalars(text("SELECT id::text FROM catalog.ingredients WHERE id=ANY(CAST(:ids AS uuid[])) AND is_active"),
                                          {"ids": sorted(desired)})).all())
        if valid != desired:
            raise HTTPException(422, "В списке есть недоступные ингредиенты")
        await self.db.execute(text("DELETE FROM public.user_ingredient_exclusions WHERE user_id=:user"), {"user": self.user_id})
        await self.add(sorted(valid))
        await self.db.execute(text("UPDATE public.user_preferences SET revision=revision+1, updated_at=now() WHERE user_id=:user"), {"user": self.user_id})
        return await self.read()

    async def add(self, ids: list[str]):
        await self.db.execute(text("""
            INSERT INTO public.user_ingredient_exclusions(user_id, ingredient_id)
            SELECT :user, unnest(CAST(:ids AS uuid[])) ON CONFLICT DO NOTHING
        """), {"user": self.user_id, "ids": ids})

    async def import_local(self, ids: list[str]) -> PersonalPreferences:
        row = await self.lock()
        if row["local_imported_at"]:
            return await self.read()
        if any(len(value) > 200 for value in ids):
            raise HTTPException(422, "Некорректный идентификатор ингредиента")
        matches = (await self.db.execute(text("""
            SELECT i.id::text AS requested, i.id::text AS canonical FROM catalog.ingredients i
            WHERE i.is_active AND i.id::text=ANY(CAST(:ids AS text[]))
            UNION
            SELECT m.legacy_ingredient_id::text, i.id::text FROM catalog.ingredient_matches m
            JOIN catalog.ingredients i ON i.id=m.ingredient_id
            WHERE i.is_active AND m.legacy_ingredient_id::text=ANY(CAST(:ids AS text[]))
            UNION
            SELECT a.alias, i.id::text FROM catalog.ingredient_aliases a JOIN catalog.ingredients i ON i.id=a.ingredient_id
            WHERE i.is_active AND a.source='legacy_preference_id' AND a.alias=ANY(CAST(:ids AS text[]))
            UNION
            SELECT a.alias, i.id::text FROM catalog.ingredient_alias_review a JOIN catalog.ingredients i ON i.id=a.ingredient_id
            WHERE i.is_active AND a.source='legacy_preference_id' AND a.alias=ANY(CAST(:ids AS text[]))
        """), {"ids": ids})).mappings().all()
        candidates: dict[str, set[str]] = {}
        for match in matches:
            candidates.setdefault(match["requested"], set()).add(match["canonical"])
        resolved = {key: next(iter(values)) for key, values in candidates.items() if len(values) == 1}
        await self.add(sorted(set(resolved.values())))
        await self.db.execute(text("""
            UPDATE public.user_preferences SET local_imported_at=now(), updated_at=now(), revision=revision+1 WHERE user_id=:user
        """), {"user": self.user_id})
        result = await self.read()
        result.unresolvedIds = sorted(set(ids) - resolved.keys())
        return result
