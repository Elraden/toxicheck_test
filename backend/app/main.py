import logging
import asyncio
import os
from contextlib import asynccontextmanager
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

    @asynccontextmanager
    async def lifespan(app):
        from app.services.orchestration import recover, live_tasks
        try:
            if os.environ.get('ORCHESTRATION_RECOVER_ON_STARTUP') == 'true':
                await recover()
        except Exception as exc:
            logging.getLogger(__name__).warning('Experiment recovery unavailable: %s', type(exc).__name__)
        yield
        if live_tasks:
            await asyncio.gather(*list(live_tasks), return_exceptions=True)

    app = FastAPI(
        lifespan=lifespan,
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

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/api/auth/", "/api/admin/", "/api/preferences/me", "/api/research/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    async def database_error_handler(request, error):
        logging.getLogger(__name__).error("Database request failed: %s", type(error).__name__)
        return JSONResponse(status_code=503, content={"detail": "База ингредиентов временно недоступна. Повторите проверку позже."})

    app.add_exception_handler(SQLAlchemyError, database_error_handler)
    app.add_exception_handler(asyncpg.PostgresError, database_error_handler)
    app.add_exception_handler(ConnectionError, database_error_handler)
    app.add_exception_handler(TimeoutError, database_error_handler)

    return app


app = create_app()
