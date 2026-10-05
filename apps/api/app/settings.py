"""Platform API settings, read from the environment only.

Secrets are never written to disk by this module and never logged. Defaults
exist so a developer can start the service locally; deployments must inject
real values through container environment variables.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: str = "dev"
    service_name: str = "rubai-api"
    database_url: str = "postgresql+asyncpg://rubai:rubai@127.0.0.1:5432/rubai"
    redis_url: str = "redis://127.0.0.1:6379/0"


settings = Settings()
