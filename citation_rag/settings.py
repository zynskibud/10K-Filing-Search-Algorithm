import os

from pydantic import field_validator
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

    @field_validator("hf_home")
    @classmethod
    def _hf_home_absolute(cls, value: str) -> str:
        """Integration-1 item 6: always an absolute path, whatever `.env`
        holds (already absolute here, but a relative value or one with a
        trailing slash or `..` segment is normalized too), so every model
        loader that reads `Settings().hf_home` can pass it straight to
        `cache_folder=`/`cache_dir=` without its own normalization."""
        return os.path.abspath(os.path.expanduser(value))
