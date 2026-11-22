"""Concrete HTTP adapter restricted to GitHub/Render GET and named read-only MCP calls.

Inject credentials from a trusted runtime secret store. No endpoint here merges,
deploys, changes Render settings or writes financial records.
"""

from typing import Any
from urllib.parse import urlsplit

import httpx


class ReadOnlyHttpAdapter:
    def __init__(
        self,
        *,
        repo: str,
        service_id: str,
        render_token: str,
        mcp_url: str,
        trusted_mcp_origin: str,
        mcp_oauth_token: str,
        github_token: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlsplit(mcp_url)
        trusted = urlsplit(trusted_mcp_origin)
        same_origin = (
            parsed.scheme == trusted.scheme == "https"
            and parsed.hostname == trusted.hostname
            and parsed.port == trusted.port
            and bool(parsed.hostname)
            and trusted.path in ("", "/")
            and not trusted.username
            and not trusted.password
            and not trusted.query
            and not trusted.fragment
            and parsed.port in (None, 443)
        )
        if (
            not same_origin
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/mcp"
            or not repo
            or "/" not in repo
            or not service_id.startswith("srv-")
            or not render_token
            or not mcp_oauth_token
        ):
            raise ValueError("INVALID_READ_ONLY_ADAPTER_CONFIGURATION")
        owner, name = repo.split("/", 1)
        if not owner.isascii() or not name.isascii():
            raise ValueError("INVALID_REPOSITORY")
        if not all(c.isalnum() or c in "._-" for c in owner + name):
            raise ValueError("INVALID_REPOSITORY")
        if not all(c.isalnum() or c in "-" for c in service_id):
            raise ValueError("INVALID_SERVICE_ID")
        self._repo = repo
        self._service_id = service_id
        self._render_token = render_token
        self._mcp_url = mcp_url
        self._mcp_token = mcp_oauth_token
        self._github_token = github_token
        self._client = (
            client if client is not None else httpx.Client(timeout=15.0, follow_redirects=False)
        )

    def _get(self, url: str, token: str | None) -> Any:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        response = self._client.get(url, headers=headers, follow_redirects=False)
        response.raise_for_status()
        result: Any = response.json()
        return result

    def render_service(self) -> dict[str, Any]:
        url = "https://api.render.com/v1/services/" + self._service_id
        result = self._get(url, self._render_token)
        data = result.get("service", result) if isinstance(result, dict) else None
        if not isinstance(data, dict) or data.get("id") != self._service_id:
            raise ValueError("INVALID_RENDER_SERVICE_RESPONSE")
        return data

    def main_commit(self) -> str:
        result = self._get(
            "https://api.github.com/repos/" + self._repo + "/branches/main",
            self._github_token,
        )
        value = result.get("commit", {}).get("sha") if isinstance(result, dict) else None
        if not isinstance(value, str) or len(value) != 40:
            raise ValueError("INVALID_GITHUB_COMMIT_RESPONSE")
        return value

    def latest_deployment(self) -> dict[str, Any]:
        result = self._get(
            "https://api.render.com/v1/services/" + self._service_id + "/deploys?limit=1",
            self._render_token,
        )
        if not isinstance(result, list) or not result:
            raise ValueError("MISSING_RENDER_DEPLOY")
        item = result[0]
        deployment = item.get("deploy", item) if isinstance(item, dict) else None
        if not isinstance(deployment, dict):
            raise ValueError("INVALID_RENDER_DEPLOY_RESPONSE")
        return deployment

    def _mcp_request(self, message: dict[str, Any], *, session: str | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": "Bearer " + self._mcp_token,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if session is not None:
            headers["Mcp-Session-Id"] = session
        response = self._client.post(
            self._mcp_url, headers=headers, json=message, follow_redirects=False
        )
        response.raise_for_status()
        if message.get("method") == "notifications/initialized":
            if response.status_code != 202:
                raise ValueError("MCP_NOTIFICATION_NOT_ACCEPTED")
            return {"jsonrpc": "2.0"}
        if "application/json" not in response.headers.get("content-type", ""):
            raise ValueError("UNEXPECTED_MCP_TRANSPORT")
        payload: Any = response.json()
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            raise ValueError("INVALID_MCP_RESPONSE")
        if "error" in payload:
            raise ValueError("MCP_READ_FAILED")
        if "id" in message and payload.get("id") != message["id"]:
            raise ValueError("MCP_RESPONSE_ID_MISMATCH")
        return payload

    def _mcp_read(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in {"plugin_info", "financial_audit_snapshot", "compare_financial_audit"}:
            raise ValueError("MCP_TOOL_NOT_ALLOWLISTED")
        initialized = self._mcp_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pet-finance-deploy-audit", "version": "1.0.0"},
            },
        })
        handshake = initialized.get("result")
        if (
            not isinstance(handshake, dict)
            or handshake.get("protocolVersion") != "2025-06-18"
            or not isinstance(handshake.get("capabilities"), dict)
        ):
            raise ValueError("MCP_HANDSHAKE_REJECTED")
        # Stateless FastMCP transport: the server does not require a session cookie.
        self._mcp_request({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        reply = self._mcp_request({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })
        result = reply.get("result")
        if not isinstance(result, dict) or result.get("isError"):
            raise ValueError("MCP_TOOL_FAILED")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise ValueError("MCP_STRUCTURED_RESULT_REQUIRED")
        return structured

    def plugin_info(self) -> dict[str, Any]:
        return self._mcp_read("plugin_info", {})

    def financial_snapshot(self) -> dict[str, Any]:
        return self._mcp_read("financial_audit_snapshot", {})

    def compare_financial_audit(self, baseline: dict[str, Any]) -> dict[str, Any]:
        return self._mcp_read("compare_financial_audit", {"baseline": baseline})
