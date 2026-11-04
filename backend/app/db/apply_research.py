"""Apply the research migration explicitly; never run during app startup."""
import argparse
import asyncio
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings


async def migrate(host: str, apply: bool):
    if make_url(settings.catalog_url).host != host:
        raise SystemExit('Хост БД не совпадает. Изменения не выполнены.')
    engine = create_async_engine(settings.catalog_url)
    try:
        async with engine.begin() as db:
            await db.execute(text("SET LOCAL lock_timeout = '5s'"))
            if not await db.scalar(text("SELECT to_regclass('public.user_sessions')")):
                raise SystemExit('Сначала примените миграцию идентификации.')
            if not apply:
                print('Проверка выполнена. Для применения добавьте --apply.')
                return
            raw = await db.get_raw_connection()
            sql = Path(__file__).resolve().parents[2] / 'db/migrations/20261030_research.sql'
            await raw.driver_connection.execute(sql.read_text(encoding='utf-8'))
        print('Миграция исследования применена. Каталог не изменён.')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-host', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        asyncio.run(migrate(args.expected_host, args.apply))
    except Exception as exc:
        raise SystemExit(f'Ошибка миграции: {type(exc).__name__}. Транзакция отменена.') from None
