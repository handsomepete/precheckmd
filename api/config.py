"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    database_url: str = "postgresql+asyncpg://nox:nox@postgres:5432/nox"
    redis_url: str = "redis://redis:6379/0"
    artifact_dir: str = "/artifacts"
    api_key: str = "changeme-api-key"

    # Token budgets (per job)
    max_input_tokens: int = 200_000
    max_output_tokens: int = 50_000

    # Wall-clock timeout per job in seconds
    job_timeout_seconds: int = 900  # 15 minutes


settings = Settings()
