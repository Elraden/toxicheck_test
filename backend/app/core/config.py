from functools import cached_property

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ToxiCheck API"
    app_env: str = "local"
    database_url: str = Field(
        default="postgresql+asyncpg://toxicheck:toxicheck@localhost:5432/toxicheck"
    )
    catalog_database_url: str | None = Field(default=None, validation_alias="TOXICHECK_CATALOG_DATABASE_URL")
    frontend_origins_raw: str = Field(
        default=(
            "http://localhost:5173,"
            "http://127.0.0.1:5173,"
            "https://toxicheck-ruby.vercel.app"
        ),
        alias="FRONTEND_ORIGINS",
    )
    open_food_facts_base_url: str = "https://world.openfoodfacts.org"
    ocr_mode: str = "embedded"
    ocr_service_url: str | None = None

    @model_validator(mode="after")
    def normalize_database_url(self) -> "Settings":
        for field in ("database_url", "catalog_database_url"):
            value = getattr(self, field)
            if value and value.startswith(("postgresql://", "postgres://")):
                value = "postgresql+asyncpg://" + value.split("://", 1)[1]
            if value:
                url = make_url(value)
                if url.drivername == "postgresql+asyncpg" and "sslmode" in url.query:
                    mode = url.query["sslmode"]
                    url = url.difference_update_query(["sslmode"])
                    if "ssl" not in url.query:
                        url = url.update_query_dict({"ssl": mode})
                setattr(self, field, url.render_as_string(hide_password=False))

        return self

    @property
    def catalog_url(self) -> str:
        return self.catalog_database_url or self.database_url

    @cached_property
    def frontend_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.frontend_origins_raw.split(",")
            if origin.strip()
        ]


settings = Settings()
