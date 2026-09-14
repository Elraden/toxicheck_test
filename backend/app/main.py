import logging
import asyncpg

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.router import api_router
from app.core.config import settings


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api")

    async def database_error_handler(request, error):
        logging.getLogger(__name__).error("Database request failed: %s", type(error).__name__)
        return JSONResponse(status_code=503, content={"detail": "База ингредиентов временно недоступна. Повторите проверку позже."})

    app.add_exception_handler(SQLAlchemyError, database_error_handler)
    app.add_exception_handler(asyncpg.PostgresError, database_error_handler)
    app.add_exception_handler(ConnectionError, database_error_handler)

    return app


app = create_app()
