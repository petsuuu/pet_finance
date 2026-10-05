import base64
import hashlib
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.db.database import build_engine
from app.main import create_app
from app.services.oauth import OwnerOAuth, digest

ORIGIN = "https://pet-finance.example"
CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"
PASSWORD = "fictional-connection-password-for-tests"
VERIFIER = "a" * 64
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
)


def config() -> Settings:
    return Settings(mcp_public_url=ORIGIN, mcp_login_password=PASSWORD)


@pytest.fixture()
def mcp_client(client: TestClient) -> Iterator[TestClient]:
    engine = build_engine(config().database_url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE oauth_records"))
    engine.dispose()
    with TestClient(create_app(config()), base_url=ORIGIN) as c:
        yield c


def grant(client: TestClient) -> tuple[str, str]:
    registered = client.post("/oauth/register", json={"redirect_uris": [CALLBACK]})
    assert registered.status_code == 201
    identity = registered.json()["client_id"]
    page = client.get(
        "/oauth/authorize",
        params={
            "client_id": identity,
            "redirect_uri": CALLBACK,
            "resource": ORIGIN + "/mcp",
            "response_type": "code",
            "code_challenge_method": "S256",
            "code_challenge": CHALLENGE,
            "state": "fictional-state",
        },
    )
    assert page.status_code == 200
    assert page.headers["referrer-policy"] == "strict-origin"
    assert PASSWORD not in page.text
    login = re.search(r'name="login" value="([^"]+)"', page.text)
    assert login
    response = client.post(
        "/oauth/approve",
        data={"login": login[1], "password": PASSWORD},
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["iss"] == [ORIGIN]
    assert query["state"] == ["fictional-state"]
    return identity, query["code"][0]


def exchange(client: TestClient, identity: str, code: str, **changes: str) -> Any:
    return client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": identity,
            "code": code,
            "code_verifier": VERIFIER,
            "redirect_uri": CALLBACK,
            "resource": ORIGIN + "/mcp",
            **changes,
        },
    )


def rpc(client: TestClient, token: str, method: str, params: dict[str, Any]) -> Any:
    return client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )


def test_oauth_discovery_and_authentication_required(mcp_client: TestClient) -> None:
    metadata = mcp_client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
    assert metadata["authorization_response_iss_parameter_supported"] is True
    assert mcp_client.get("/.well-known/oauth-protected-resource/mcp").json()["resource"] == (
        ORIGIN + "/mcp"
    )
    response = rpc(mcp_client, "test-token", "tools/list", {})
    assert response.status_code == 401
    assert "oauth-protected-resource/mcp" in response.headers["www-authenticate"]
    assert mcp_client.get("/api/v1/accounts").status_code == 401


@pytest.mark.parametrize(
    "changes",
    [
        {"code_verifier": "b" * 64},
        {"resource": "https://other.example/mcp"},
        {"redirect_uri": "https://other.example/callback"},
        {"client_id": "other-client"},
    ],
)
def test_oauth_rejects_wrong_grant_binding(
    mcp_client: TestClient,
    changes: dict[str, str],
) -> None:
    identity, code = grant(mcp_client)
    assert exchange(mcp_client, identity, code, **changes).status_code == 400
    assert exchange(mcp_client, identity, code).status_code == 200
    assert exchange(mcp_client, identity, code).status_code == 400


def test_oauth_callback_and_csrf(mcp_client: TestClient) -> None:
    for callback in [
        "https://evil.example/callback",
        CALLBACK + "?evil=1",
        "https://chatgpt.com.evil.example/connector_platform_oauth_redirect",
    ]:
        assert (
            mcp_client.post("/oauth/register", json={"redirect_uris": [callback]}).status_code
            == 400
        )
    identity = mcp_client.post("/oauth/register", json={"redirect_uris": [CALLBACK]}).json()[
        "client_id"
    ]
    page = mcp_client.get(
        "/oauth/authorize",
        params={
            "client_id": identity,
            "redirect_uri": CALLBACK,
            "resource": ORIGIN + "/mcp",
            "response_type": "code",
            "code_challenge_method": "S256",
            "code_challenge": CHALLENGE,
        },
    )
    login = re.search(r'name="login" value="([^"]+)"', page.text)
    assert login
    data = {"login": login[1], "password": PASSWORD}
    for headers in [{}, {"Origin": "null"}]:
        assert mcp_client.post("/oauth/approve", data=data, headers=headers).status_code == 400
    assert (
        mcp_client.post(
            "/oauth/approve", data=data, headers={"Origin": "https://evil.example"}
        ).status_code
        == 400
    )
    assert (
        mcp_client.post(
            "/oauth/approve", data={**data, "password": "incorrect"}, headers={"Origin": ORIGIN}
        ).status_code
        == 401
    )
    mcp_client.cookies.clear()
    assert (
        mcp_client.post("/oauth/approve", data=data, headers={"Origin": ORIGIN}).status_code == 400
    )


def test_oauth_persistence_refresh_expiry_and_password_rotation(mcp_client: TestClient) -> None:
    identity, code = grant(mcp_client)
    result = exchange(mcp_client, identity, code).json()
    engine = build_engine(config().database_url)
    verifier = OwnerOAuth(config(), engine)
    assert verifier.verify(result["access_token"]) is not None
    with engine.connect() as connection:
        stored = connection.execute(text("SELECT id, payload FROM oauth_records")).all()
        assert result["access_token"] not in str(stored)
        assert result["refresh_token"] not in str(stored)
        assert PASSWORD not in str(stored)
    refresh = {
        "grant_type": "refresh_token",
        "client_id": identity,
        "refresh_token": result["refresh_token"],
        "resource": ORIGIN + "/mcp",
    }
    renewed = mcp_client.post("/oauth/token", data=refresh)
    assert renewed.status_code == 200
    assert mcp_client.post("/oauth/token", data=refresh).status_code == 400
    assert (
        mcp_client.post(
            "/oauth/revoke",
            data={
                "token": renewed.json()["access_token"],
                "client_id": identity,
            },
        ).status_code
        == 200
    )
    assert verifier.verify(renewed.json()["access_token"]) is None
    rotated = Settings(mcp_public_url=ORIGIN, mcp_login_password=PASSWORD + "-rotated")
    assert OwnerOAuth(rotated, engine).verify(result["access_token"]) is None
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE oauth_records SET expires_at=now()-interval '1 second' WHERE id=:id"),
            {"id": digest(result["access_token"])},
        )
    assert verifier.verify(result["access_token"]) is None
    engine.dispose()


def test_login_rate_limit_counts_failed_attempts(mcp_client: TestClient) -> None:
    for _ in range(20):
        assert (
            mcp_client.post(
                "/oauth/approve",
                data={"login": "invalid", "password": "incorrect"},
                headers={"Origin": ORIGIN},
            ).status_code
            == 400
        )
    assert (
        mcp_client.post(
            "/oauth/approve",
            data={"login": "invalid", "password": "incorrect"},
            headers={"Origin": ORIGIN},
        ).status_code
        == 429
    )


def test_mcp_configuration_requires_separate_password_and_https() -> None:
    cases: list[dict[str, Any]] = [
        {"mcp_public_url": ORIGIN},
        {"mcp_login_password": PASSWORD},
        {"mcp_public_url": "http://example.com", "mcp_login_password": PASSWORD},
        {"mcp_public_url": ORIGIN + "/mcp", "mcp_login_password": PASSWORD},
        {"mcp_public_url": ORIGIN, "mcp_login_password": "short"},
    ]
    for values in cases:
        with pytest.raises(ValueError):
            Settings(**values)


def test_mcp_tools_reuse_ledger_idempotency_and_payment_updates(mcp_client: TestClient) -> None:
    identity, code = grant(mcp_client)
    token = exchange(mcp_client, identity, code).json()["access_token"]
    initialized = rpc(
        mcp_client,
        token,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    )
    assert initialized.status_code == 200
    tools = rpc(mcp_client, token, "tools/list", {}).json()["result"]["tools"]
    assert len(tools) == 8
    assert all(t["_meta"]["securitySchemes"][0]["type"] == "oauth2" for t in tools)
    auth = {"Authorization": "Bearer test-token"}
    account = mcp_client.post(
        "/api/v1/accounts", json={"name": "Fictional wallet"}, headers=auth
    ).json()["id"]
    category = mcp_client.post(
        "/api/v1/categories", json={"name": "Fictional category"}, headers=auth
    ).json()["id"]
    body = {
        "type": "EXPENSE",
        "status": "PENDING",
        "description": "Fictional bill",
        "amount": "50.00",
        "transaction_date": "2026-10-05",
        "account_id": account,
        "category_id": category,
        "idempotency_key": "fictional-mcp-operation",
    }
    params = {"name": "create_transaction", "arguments": {"body": body}}
    first = rpc(mcp_client, token, "tools/call", params).json()["result"]
    assert not first.get("isError")
    transaction = first["structuredContent"]["id"]
    assert first["structuredContent"]["source"] == "CHATGPT"
    repeated = rpc(mcp_client, token, "tools/call", params).json()["result"]
    assert repeated["structuredContent"]["id"] == transaction
    duplicate = rpc(
        mcp_client,
        token,
        "tools/call",
        {
            "name": "create_transaction",
            "arguments": {"body": {**body, "idempotency_key": "other"}},
        },
    ).json()["result"]
    assert duplicate["isError"] is True
    updated = rpc(
        mcp_client,
        token,
        "tools/call",
        {
            "name": "update_transaction",
            "arguments": {"identity": transaction, "body": {"status": "POSTED", "amount": "48.50"}},
        },
    ).json()["result"]
    assert updated["structuredContent"]["status"] == "POSTED"
    transactions = mcp_client.get("/api/v1/transactions", headers=auth).json()
    assert len(transactions) == 1
    balance = rpc(
        mcp_client,
        token,
        "tools/call",
        {
            "name": "list_accounts",
            "arguments": {},
        },
    ).json()["result"]["structuredContent"]["items"][0]["current_balance"]
    assert balance == "-48.50"
