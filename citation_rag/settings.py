from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    hf_home: str
    sec_user_agent: str
    ollama_url: str
    anthropic_api_key: str | None = None

    class Config:
        env_file = ".env"
        case_sensitive = False
