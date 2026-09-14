import asyncpg
from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db")
async def database_health_check() -> dict:
    database_url = settings.database_url.replace(
        "postgresql+asyncpg://",
        "postgresql://",
        1,
    )

    try:
        connection = await asyncpg.connect(database_url, timeout=5)
        try:
            await connection.fetchval("SELECT 1")
            counts = {}
            for table in ("ingredients", "ingredient_aliases", "ingredient_rules", "regulatory_sources"):
                counts[table] = await connection.fetchval(f"SELECT count(*) FROM {table}")
        finally:
            await connection.close()
    except Exception as error:
        return {
            "status": "error",
            "database": "unavailable",
            "errorType": error.__class__.__name__,
        }

    return {"status": "ok", "database": "available", "counts": counts}
