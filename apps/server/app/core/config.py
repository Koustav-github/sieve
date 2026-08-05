from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://sieve:sieve@localhost:5433/sieve"
    cors_origins: list[str] = ["http://localhost:3000"]
    telegram_bot_token: str = ""
    discord_bot_token: str = ""
    anthropic_api_key: str = ""


settings = Settings()
