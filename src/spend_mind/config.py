from typing import Annotated

from pydantic import Field, SecretStr, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(min_length=1)
    postgres_user: str = Field(min_length=1)
    postgres_password: SecretStr = Field(min_length=1)
    internal_api_token: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_model: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ] = "gpt-5.4-mini"
    openai_timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    openai_total_timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    openai_max_retries: int = Field(default=2, ge=0)

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
