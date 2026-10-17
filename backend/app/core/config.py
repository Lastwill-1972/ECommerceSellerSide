from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also contains Compose-only variables.
    )

    app_name: str = "Multi-merchant SaaS Ecommerce API"
    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = False
    db_host: str = "127.0.0.1"
    db_port: int = Field(default=3306, ge=1, le=65535)
    db_name: str | None = None
    db_user: str | None = None
    db_password: SecretStr | None = None
    db_connect_timeout: int = Field(default=5, ge=1, le=60)
    auth_jwt_secret: SecretStr | None = None
    auth_jwt_access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)

    @property
    def database_url(self) -> URL:
        if not self.db_name or not self.db_user or not self.db_password:
            raise ValueError("Set DB_NAME, DB_USER and DB_PASSWORD in backend/.env or environment")
        if not self.db_password.get_secret_value():
            raise ValueError("DB_PASSWORD must not be empty")
        # URL.create safely handles special characters without manual URL escaping.
        return URL.create(
            "mysql+pymysql",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
