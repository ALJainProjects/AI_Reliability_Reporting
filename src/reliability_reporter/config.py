"""Configuration management for the reliability reporter."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # AI Provider settings
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    ai_provider: Literal["openai", "anthropic"] = Field(default="openai", alias="AI_PROVIDER")

    # Model settings
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
    anthropic_model: str = Field(default="claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL")

    # Cache settings
    cache_dir: Path = Field(default=Path(".cache"), alias="CACHE_DIR")
    cache_ttl_hours: int = Field(default=24, alias="CACHE_TTL_HOURS")

    # Rate limiting
    rate_limit_requests_per_second: float = Field(
        default=1.0, alias="RATE_LIMIT_REQUESTS_PER_SECOND"
    )

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    def get_api_key(self, provider: str | None = None) -> str:
        """Get API key for the specified or default provider."""
        provider = provider or self.ai_provider
        if provider == "openai":
            if not self.openai_api_key:
                raise ValueError("OPENAI_API_KEY not set")
            return self.openai_api_key
        elif provider == "anthropic":
            if not self.anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY not set")
            return self.anthropic_api_key
        else:
            raise ValueError(f"Unknown AI provider: {provider}")


# Global settings instance
settings = Settings()
