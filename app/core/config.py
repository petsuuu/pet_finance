from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    api_token: str
    user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
