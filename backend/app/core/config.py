from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/redflow"
    redis_url: str = "redis://localhost:6379/0"
    temporal_url: str = "localhost:7233"

    jwt_secret: str = "dev-secret-change-me"
    jwt_refresh_secret: str = "dev-refresh-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    frontend_url: str = "http://localhost:3000"

    ai_provider: str = "anthropic"
    ai_api_key: str = ""
    ai_model: str = ""  # overrides the provider's default model when set
    # Only used by ai_provider=lmstudio (or any other openai-compatible provider
    # added later): the base URL of that server's OpenAI-compatible API.
    ai_base_url: str = "http://localhost:1234/v1"
    # Name of an embeddings model loaded on the same OpenAI-compatible server
    # (e.g. LM Studio) at ai_base_url. Empty = semantic knowledge-base search
    # stays off and search_articles falls back to plain ILIKE.
    ai_embedding_model: str = ""
    # $ per 1M tokens. Defaults to 0 (no fabricated pricing) — set these to your
    # actual Claude Opus 5 contract rates to get real ai_executions.estimated_cost
    # and the analytics cost total.
    ai_input_price_per_million: float = 2.0
    ai_output_price_per_million: float = 0.4

    # Fernet key used to encrypt integration credentials at rest (access tokens,
    # app secrets). Generate a real one per environment: Fernet.generate_key().
    encryption_key: str = "CYgW-8y2Xk41EMIOPdfmZ9JPBVXRJU3yQAWFaYQM3Ck="
    whatsapp_verify_token: str = "dev-verify-token"


settings = Settings()
