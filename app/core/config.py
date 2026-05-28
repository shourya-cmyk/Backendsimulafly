from functools import lru_cache
from typing import Annotated, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    SECRET_KEY: str = Field(min_length=32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    JWT_ALGORITHM: str = "HS256"

    DATABASE_URL: str

    AZURE_AI_FOUNDRY_ENDPOINT: str = ""
    AZURE_AI_FOUNDRY_API_KEY: str = ""
    AZURE_CHAT_DEPLOYMENT: str = "gpt-4o"
    AZURE_EMBEDDING_DEPLOYMENT: str = "text-embedding-3-small"
    AZURE_IMAGE_EDIT_DEPLOYMENT: str = ""
    AZURE_IMAGE_GEN_DEPLOYMENT: str = ""

    ALLOWED_ORIGINS: Annotated[List[str], NoDecode] = ["*"]

    # CSV of accepted Google OAuth client IDs (`aud` claim). For an Android
    # app, include both the Android client ID and the Web client ID used as
    # `serverClientId` from the Flutter client.
    GOOGLE_CLIENT_IDS: Annotated[List[str], NoDecode] = []

    RATE_LIMIT_PER_MINUTE: int = 60
    CHAT_RATE_LIMIT_PER_MINUTE: int = 20
    IMAGE_GEN_RATE_LIMIT_PER_HOUR: int = 10
    UPLOAD_RATE_LIMIT_PER_HOUR: int = 30

    MAX_IMAGE_BYTES: int = 5 * 1024 * 1024
    MAX_REQUEST_BYTES: int = 8 * 1024 * 1024

    # Shared secret for protected admin operations (e.g. POST/PATCH/DELETE
    # /styles/). Sent by the admin via the `X-Admin-Key` request header.
    # Empty / unset → admin endpoints return 503 (disabled).
    ADMIN_API_KEY: str = ""

    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

    @field_validator("ALLOWED_ORIGINS", "GOOGLE_CLIENT_IDS", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.ENV.lower() == "production"

    @property
    def ai_configured(self) -> bool:
        return bool(self.AZURE_AI_FOUNDRY_ENDPOINT and self.AZURE_AI_FOUNDRY_API_KEY)


@lru_cache
def get_settings() -> Settings:
    return Settings()
