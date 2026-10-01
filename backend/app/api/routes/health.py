from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_catalog_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db")
async def database_health_check(session: AsyncSession = Depends(get_catalog_session)) -> dict:
    tables = ("ingredients", "ingredient_aliases", "ingredient_rules", "regulatory_sources")
    query = " UNION ALL ".join(
        f"SELECT '{table}' AS name, count(*) AS total FROM catalog.{table}"
        for table in tables
    )
    rows = (await session.execute(text(query))).mappings().all()
    return {"status": "ok", "database": "available", "schema": "catalog", "schema_version": 2,
            "counts": {r["name"]: r["total"] for r in rows}}
