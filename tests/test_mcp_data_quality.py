"""MCP access to data-quality diagnostics must be authenticated and read-only."""

from fastapi.testclient import TestClient

from tests.test_mcp import exchange, grant, mcp_client, rpc


def test_mcp_quality_dashboard_is_read_only(mcp_client: TestClient) -> None:
    identity, code = grant(mcp_client)
    token = exchange(mcp_client, identity, code).json()["access_token"]
    tools = rpc(mcp_client, token, "tools/list", {}).json()["result"]["tools"]
    quality = next(tool for tool in tools if tool["name"] == "data_quality_dashboard")
    assert quality["annotations"]["readOnlyHint"] is True
    assert quality["_meta"]["securitySchemes"][0]["type"] == "oauth2"

    account = mcp_client.post(
        "/api/v1/accounts",
        headers={"Authorization": "Bearer test-token"},
        json={"name": "Test wallet", "opening_balance": "100.00"},
    )
    assert account.status_code == 201, account.text

    transactions_before = mcp_client.get(
        "/api/v1/transactions", headers={"Authorization": "Bearer test-token"}
    ).json()
    history_before = mcp_client.get(
        "/api/v1/audit/transactions", headers={"Authorization": "Bearer test-token"}
    ).json()
    first = rpc(
        mcp_client,
        token,
        "tools/call",
        {"name": "data_quality_dashboard", "arguments": {"limit": 5}},
    ).json()["result"]
    assert not first.get("isError"), first
    assert first["structuredContent"]["read_only"] is True
    assert first["structuredContent"]["status"] == "OK"
    assert first["structuredContent"]["summary"]["accounts_checked"] == 1
    assert first["structuredContent"]["summary"]["total_findings"] == 0
    assert first["structuredContent"]["sections"][0]["id"] == "balances"
    assert mcp_client.get(
        "/api/v1/transactions", headers={"Authorization": "Bearer test-token"}
    ).json() == transactions_before
    assert mcp_client.get(
        "/api/v1/audit/transactions", headers={"Authorization": "Bearer test-token"}
    ).json() == history_before


def test_mcp_quality_dashboard_rejects_invalid_limit(mcp_client: TestClient) -> None:
    identity, code = grant(mcp_client)
    token = exchange(mcp_client, identity, code).json()["access_token"]
    for value in (0, 501):
        response = rpc(
            mcp_client,
            token,
            "tools/call",
            {"name": "data_quality_dashboard", "arguments": {"limit": value}},
        ).json()["result"]
        assert response.get("isError") is True


def test_mcp_quality_dashboard_requires_authentication(mcp_client: TestClient) -> None:
    assert rpc(
        mcp_client,
        "test-token",
        "tools/call",
        {"name": "data_quality_dashboard", "arguments": {}},
    ).status_code == 401
