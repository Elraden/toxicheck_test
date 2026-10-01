from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text
from fastapi import HTTPException

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)

catalog_engine = create_async_engine(
    settings.catalog_url,
    pool_pre_ping=True,
    connect_args={"timeout": 10, "command_timeout": 20,
                  "server_settings": {"default_transaction_read_only": "on"}},
)
CatalogSessionLocal = async_sessionmaker(bind=catalog_engine, expire_on_commit=False)


async def get_catalog_session() -> AsyncGenerator[AsyncSession]:
    async with CatalogSessionLocal() as session:
        version = await session.scalar(text("""
            SELECT version FROM catalog.schema_version
            WHERE EXISTS (SELECT 1 FROM catalog.ingredients WHERE is_active)
        """))
        if version != 2:
            raise HTTPException(status_code=503, detail="Справочник версии 2 не загружен или несовместим с приложением.")
        yield session


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
