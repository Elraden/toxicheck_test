import asyncio
from pathlib import Path

import asyncpg

from app.core.config import settings


def _get_asyncpg_url() -> str:
    return settings.database_url.replace(
        "postgresql+asyncpg://",
        "postgresql://",
        1,
    )


async def apply_schema() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")

    connection = await asyncpg.connect(_get_asyncpg_url())

    try:
        await connection.execute(schema_sql)
    finally:
        await connection.close()


def main() -> None:
    asyncio.run(apply_schema())


if __name__ == "__main__":
    main()
