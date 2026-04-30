from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API keys
    anthropic_api_key: str
    openai_api_key: str
    gemini_api_key: str

    # ChromaDB (persistent embedded client)
    chroma_db_path: str = "./data/chroma_db"
    chromadb_collection_courses: str = "usc_courses"
    chromadb_collection_policy: str = "usc_policy"

    # Validator / scheduler loop
    max_validator_iterations: int = 3

    # Retry limits for LLM calls
    max_description_retries: int = 2
    max_retrieval_retries: int = 2

    # Enrichment tuning
    max_enrichment_expansion_depth: int = 1
    min_enrichment_courses: int = 5

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
