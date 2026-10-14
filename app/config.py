from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./apexpulse.db"
    secret_key: str = "dev-secret-change-me"
    access_token_minutes: int = 30
    refresh_token_days: int = 30
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    ai_chat_daily_limit: int = 50
    cors_origins: str = "http://localhost:8081,http://localhost:19006,http://localhost:3000"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
