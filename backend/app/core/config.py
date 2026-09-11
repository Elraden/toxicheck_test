from functools import cached_property

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        if self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace(
                "postgresql://",
                "postgresql+asyncpg://",
                1,
            )

        return self

    @cached_property
    def frontend_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.frontend_origins_raw.split(",")
            if origin.strip()
        ]


settings = Settings()
