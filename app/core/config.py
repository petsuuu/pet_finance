from uuid import UUID

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    api_token: str
    mcp_public_url: str | None = None
    mcp_login_password: SecretStr | None = None
    user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def mcp_configuration(self) -> "Settings":
        if bool(self.mcp_public_url) != bool(self.mcp_login_password):
            raise ValueError("Set both MCP_PUBLIC_URL and MCP_LOGIN_PASSWORD to enable MCP")
        if self.mcp_public_url:
            from urllib.parse import urlsplit

            url = urlsplit(self.mcp_public_url)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in ("", "/")
            ):
                raise ValueError("MCP_PUBLIC_URL must be the HTTPS origin, without a path")
            self.mcp_public_url = self.mcp_public_url.rstrip("/")
            assert self.mcp_login_password is not None
            password = self.mcp_login_password.get_secret_value()
            if len(password) < 24 or password == self.api_token:
                raise ValueError(
                    "MCP_LOGIN_PASSWORD must be distinct from API_TOKEN, 24+ characters"
                )
        return self
