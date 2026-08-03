from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://sieve:sieve@localhost:5433/sieve"
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
