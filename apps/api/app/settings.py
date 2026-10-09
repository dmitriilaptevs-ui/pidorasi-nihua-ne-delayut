"""Platform API settings, read from the environment only.

Secrets are never written to disk by this module and never logged. Defaults
exist so a developer can start the service locally; deployments must inject
real values through container environment variables.
"""

from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: str = "dev"
    service_name: str = "rubai-api"
    database_url: str = "postgresql+asyncpg://rubai:rubai@127.0.0.1:5432/rubai"
    redis_url: str = "redis://127.0.0.1:6379/0"
    api_proxy_token: str = ""
    agent_runtime_url: str = ""
    agent_runtime_token: str = ""

    # Public web origin: cookie scope, links inside emails, Origin checks.
    public_origin: str = "http://localhost:3001"
    session_ttl_hours: int = 720
    session_cookie_name: str = "rb_platform_session"

    email_verify_ttl_hours: int = 24
    password_reset_ttl_hours: int = 2
    password_min_length: int = 10

    # "console" prints mail to the service log (development); "smtp" sends it.
    mail_transport: str = "console"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@localhost"
    smtp_starttls: bool = True

    # Fixed-window throttles, per key (email+IP / IP / email).
    throttle_login_limit: int = 10
    throttle_login_window_minutes: int = 15
    throttle_register_limit: int = 5
    throttle_register_window_minutes: int = 60
    throttle_reset_limit: int = 3
    throttle_reset_window_minutes: int = 60

    # OAuth providers (values are injected at runtime, never committed).
    vk_client_id: str = ""
    vk_app_type: str = "public"
    vk_service_token: str = ""
    yandex_client_id: str = ""
    yandex_client_secret: str = ""
    oauth_bind_cookie_name: str = "rb_oauth_bind"
    oauth_handshake_ttl_minutes: int = 10

    # Catalog and commercial pricing. FX and markup are configurable; the
    # defaults are placeholders until the owner fixes the commercial policy
    # (DECISIONS.md D-05).
    openrouter_api_base: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    provider_key_encryption_key: str = ""
    fx_rate_rub_per_usd: Decimal = Decimal("100.0")
    price_markup: Decimal = Decimal("1.20")

    # Gateway guardrails: bounded output and conservative input estimates.
    gateway_default_max_tokens: int = 1024
    gateway_max_output_tokens: int = 4096
    gateway_max_input_chars: int = 200_000
    gateway_timeout_seconds: float = 120.0
    gateway_reserve_ttl_seconds: int = 600

    # Payments. Sandbox only by policy (ADR-0004); "fake" is a local provider
    # used by tests, "yookassa" talks to the documented API.
    payments_provider: str = "yookassa"
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_api_base: str = "https://api.yookassa.ru/v3"
    payments_min_kopecks: int = 10000
    payments_max_kopecks: int = 10_000_000


settings = Settings()
