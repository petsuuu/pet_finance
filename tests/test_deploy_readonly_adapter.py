"""Only approved GitHub/Render GET and MCP read-only tool calls are allowed."""

from typing import Any

import httpx
import pytest

from app.services.deploy_readonly_adapter import ReadOnlyHttpAdapter


def adapter(handler: Any) -> ReadOnlyHttpAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ReadOnlyHttpAdapter(
        repo="petsuuu/pet_finance",
        service_id="srv-test123",
        render_token="render-secret-token",
        mcp_url="https://pet-finance.example/mcp",
        mcp_oauth_token="mcp-secret-token",
        github_token="github-secret-token",
        client=client,
    )


def test_adapter_uses_exact_hosts_methods_and_allowed_tools() -> None:
    requests: list[tuple[str, str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        auth = request.headers.get("Authorization", "")
        requests.append((request.method, host, auth))
        if host == "api.github.com":
            assert request.method == "GET"
            assert request.url.path == "/repos/petsuuu/pet_finance/branches/main"
            return httpx.Response(200, json={"commit": {"sha": "a" * 40}})
        if host == "api.render.com":
            assert request.method == "GET"
            if request.url.path.endswith("/deploys"):
                return httpx.Response(
                    200, json=[{"deploy": {"status": "live", "commit": {"id": "a" * 40}}}]
                )
            return httpx.Response(200, json={"service": {"id": "srv-test123"}})
        assert host == "pet-finance.example"
        assert request.method == "POST"
        assert request.url.path == "/mcp"
        body = __import__("json").loads(request.content)
        assert body["method"] == "tools/call"
        assert body["params"]["name"] in {
            "plugin_info",
            "financial_audit_snapshot",
            "compare_financial_audit",
        }
        assert request.headers["MCP-Protocol-Version"] == "2025-11-25"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"isError": False, "structuredContent": {"read_only": True}},
            },
        )

    source = adapter(respond)
    assert source.main_commit() == "a" * 40
    assert source.render_service()["id"] == "srv-test123"
    assert source.latest_deployment()["status"] == "live"
    assert source.plugin_info()["read_only"] is True
    assert source.financial_snapshot()["read_only"] is True
    assert source.compare_financial_audit({"snapshot_hash": "x"})["read_only"] is True
    assert requests == [
        ("GET", "api.github.com", "Bearer github-secret-token"),
        ("GET", "api.render.com", "Bearer render-secret-token"),
        ("GET", "api.render.com", "Bearer render-secret-token"),
        ("POST", "pet-finance.example", "Bearer mcp-secret-token"),
        ("POST", "pet-finance.example", "Bearer mcp-secret-token"),
        ("POST", "pet-finance.example", "Bearer mcp-secret-token"),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://pet-finance.example/mcp",
        "https://someuser:password@pet-finance.example/mcp",
        "https://pet-finance.example/other",
        "https://pet-finance.example/mcp?token=secret",
    ],
)
def test_adapter_rejects_unsafe_configuration(url: str) -> None:
    with pytest.raises(ValueError):
        ReadOnlyHttpAdapter(
            repo="petsuuu/pet_finance",
            service_id="srv-test123",
            render_token="not-a-real-secret",
            mcp_url=url,
            mcp_oauth_token="not-a-real-secret",
        )


def test_mcp_failure_is_not_treated_as_success() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "unauthorized"}}
        )

    with pytest.raises(ValueError, match="MCP_READ_FAILED"):
        adapter(respond).plugin_info()


def test_adapter_does_not_follow_redirects() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    with pytest.raises(httpx.HTTPStatusError):
        adapter(respond).plugin_info()
