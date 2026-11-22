import base64
import hashlib
import re
import tomllib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Route, sync_playwright
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


def test_mcp_plugin_version_is_semver_traceable_and_read_only(
    mcp_client: TestClient,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    package = tomllib.loads((project_root / "pyproject.toml").read_text())
    expected = package["project"]["version"]
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", expected)
    assert package_version("pet-finance") == expected
    assert f"## [{expected}]" in (project_root / "CHANGELOG.md").read_text()

    identity, code = grant(mcp_client)
    token = exchange(mcp_client, identity, code).json()["access_token"]
    initialized = rpc(
        mcp_client,
        token,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "version-check", "version": "1"},
        },
    )
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()["result"]["serverInfo"]["version"] == expected

    tools = rpc(mcp_client, token, "tools/list", {}).json()["result"]["tools"]
    info = next(tool for tool in tools if tool["name"] == "plugin_info")
    assert info["annotations"]["readOnlyHint"] is True
    assert info["_meta"]["securitySchemes"][0]["type"] == "oauth2"

    headers = {"Authorization": "Bearer test-token"}
    before_accounts = mcp_client.get("/api/v1/accounts", headers=headers).json()
    before_transactions = mcp_client.get("/api/v1/transactions", headers=headers).json()
    before_audit = mcp_client.get("/api/v1/audit/transactions", headers=headers).json()

    response = rpc(mcp_client, token, "tools/call", {"name": "plugin_info", "arguments": {}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert not result.get("isError"), result
    assert result["structuredContent"] == {
        "name": "Pet Finance",
        "version": expected,
        "api_version": "v1",
        "versioning": "semver",
        "read_only": True,
    }
    assert mcp_client.get("/api/v1/accounts", headers=headers).json() == before_accounts
    assert mcp_client.get("/api/v1/transactions", headers=headers).json() == before_transactions
    assert mcp_client.get("/api/v1/audit/transactions", headers=headers).json() == before_audit


def test_mcp_get_queries_do_not_leak_internal_rest(
    mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP tool calls must send only public named params to the REST API."""
    identity, code = grant(mcp_client)
    token = exchange(mcp_client, identity, code).json()["access_token"]
    original = httpx.AsyncClient.request
    seen: dict[str, dict[str, str]] = {}

    async def track(
        self: httpx.AsyncClient, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        if method == "GET" and url.startswith("/api/v1/"):
            seen[url] = dict(kwargs.get("params") or {})
        return await original(self, method, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "request", track)
    queries: list[tuple[str, dict[str, Any], str, dict[str, str]]] = [
        (
            "list_transactions",
            {"archive_mode": "archived", "limit": 7, "offset": 3},
            "/api/v1/transactions",
            {"archive_mode": "archived", "limit": "7", "offset": "3"},
        ),
        (
            "monthly_dashboard",
            {"year": 2026, "month": 11, "as_of": "2026-11-22"},
            "/api/v1/dashboard/monthly",
            {"year": "2026", "month": "11", "as_of": "2026-11-22"},
        ),
        (
            "monthly_agenda",
            {"year": 2026, "month": 11, "as_of": "2026-11-22"},
            "/api/v1/dashboard/agenda",
            {"year": "2026", "month": "11", "as_of": "2026-11-22"},
        ),
        (
            "daily_cashflow",
            {"as_of": "2026-11-22", "safety_margin": "25"},
            "/api/v1/dashboard/cashflow",
            {"as_of": "2026-11-22", "safety_margin": "25"},
        ),
        (
            "recovery_plan",
            {"safety_margin": "25", "estimated_bank_charges": "5"},
            "/api/v1/dashboard/recovery-plan",
            {"safety_margin": "25", "estimated_bank_charges": "5"},
        ),
        (
            "check_purchase",
            {"amount": "20", "safety_margin": "25"},
            "/api/v1/dashboard/purchase-check",
            {"amount": "20", "safety_margin": "25"},
        ),
    ]
    before_accounts = mcp_client.get(
        "/api/v1/accounts", headers={"Authorization": "Bearer test-token"}
    ).json()
    before_transactions = mcp_client.get(
        "/api/v1/transactions", headers={"Authorization": "Bearer test-token"}
    ).json()
    before_history = mcp_client.get(
        "/api/v1/audit/transactions", headers={"Authorization": "Bearer test-token"}
    ).json()

    for name, arguments, path, required in queries:
        result = rpc(mcp_client, token, "tools/call", {"name": name, "arguments": arguments})
        assert result.status_code == 200, result.text
        assert not result.json()["result"].get("isError"), (name, result.text)
        assert path in seen
        assert "rest" not in seen[path]
        for key, value in required.items():
            assert seen[path][key] == value
        assert set(seen[path]).issubset(
            set(required) | {"start_date", "end_date", "search", "status", "safety_margin"}
        )

    headers = {"Authorization": "Bearer test-token"}
    assert mcp_client.get("/api/v1/accounts", headers=headers).json() == before_accounts
    assert mcp_client.get("/api/v1/transactions", headers=headers).json() == before_transactions
    assert mcp_client.get("/api/v1/audit/transactions", headers=headers).json() == before_history


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


def test_browser_form_origin_and_csrf_cookie(mcp_client: TestClient) -> None:
    """Replay HTTPS requests locally: Chromium supplies real Origin and secure cookies.

    No requests leave the test process, and only fictional test credentials are used.
    Reproduce the previous policy failure, then exercise the corrected form.
    """
    identity = mcp_client.post("/oauth/register", json={"redirect_uris": [CALLBACK]}).json()[
        "client_id"
    ]
    authorize_url = (
        ORIGIN
        + "/oauth/authorize?"
        + urlencode(
            {
                "client_id": identity,
                "redirect_uri": CALLBACK,
                "resource": ORIGIN + "/mcp",
                "response_type": "code",
                "code_challenge_method": "S256",
                "code_challenge": CHALLENGE,
            }
        )
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for old_policy in (True, False):
            observed: dict[str, Any] = {}

            def forward(route: Route) -> None:
                request = route.request
                if urlsplit(request.url).netloc != urlsplit(ORIGIN).netloc:
                    route.fulfill(status=200, content_type="text/html", body="Connected")
                    return
                headers = request.all_headers()
                # Only the real browser's Cookie header can satisfy the CSRF check.
                mcp_client.cookies.clear()
                response = mcp_client.request(
                    request.method,
                    request.url,
                    headers=headers,
                    content=request.post_data,
                    follow_redirects=False,
                )
                response_headers = dict(response.headers)
                if "/oauth/authorize?" in request.url and old_policy:
                    response_headers["referrer-policy"] = "no-referrer"
                if request.method == "POST":
                    observed.update(
                        origin=headers.get("origin"),
                        status=response.status_code,
                        cookie=bool(headers.get("cookie")),
                    )
                route.fulfill(
                    status=response.status_code, headers=response_headers, body=response.content
                )

            context = browser.new_context()
            context.route("**/*", forward)
            page = context.new_page()
            page.goto(authorize_url)
            page.get_by_label("Senha de conexão").fill(PASSWORD)
            with page.expect_navigation():
                page.get_by_role("button", name="Autorizar conexão").click()
            assert observed["cookie"] is True
            assert observed["origin"] == ("null" if old_policy else ORIGIN)
            assert observed["status"] == (400 if old_policy else 303)
            if not old_policy:
                assert page.url.startswith(CALLBACK + "?")
            context.close()
        browser.close()


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
    assert (
        mcp_client.post("/oauth/token", data=refresh).json()["refresh_token"]
        == renewed.json()["refresh_token"]
    )
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


@pytest.mark.parametrize("include_resource", [False, True])
def test_refresh_after_access_expiry_without_new_login(
    mcp_client: TestClient, include_resource: bool
) -> None:
    identity, code = grant(mcp_client)
    issued = exchange(mcp_client, identity, code).json()
    engine = build_engine(config().database_url)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE oauth_records SET expires_at=now()-interval '1 second' WHERE id=:id"),
            {"id": digest(issued["access_token"])},
        )
    assert rpc(mcp_client, issued["access_token"], "tools/list", {}).status_code == 401
    refresh = {
        "grant_type": "refresh_token",
        "client_id": identity,
        "refresh_token": issued["refresh_token"],
    }
    if include_resource:
        refresh["resource"] = ORIGIN + "/mcp"
    for changes in [
        {"resource": "https://other.example/mcp"},
        {"client_id": "other-client"},
    ]:
        assert mcp_client.post("/oauth/token", data={**refresh, **changes}).status_code == 400
    renewed = mcp_client.post("/oauth/token", data=refresh)
    assert renewed.status_code == 200
    assert renewed.json()["refresh_token"] != issued["refresh_token"]
    assert rpc(mcp_client, renewed.json()["access_token"], "tools/list", {}).status_code == 200
    assert (
        mcp_client.post("/oauth/token", data=refresh).json()["refresh_token"]
        == renewed.json()["refresh_token"]
    )
    # A later renewal still works with a freshly constructed server using the same DB.
    with TestClient(create_app(config()), base_url=ORIGIN) as restarted:
        refresh["refresh_token"] = renewed.json()["refresh_token"]
        assert restarted.post("/oauth/token", data=refresh).status_code == 200
    engine.dispose()


def test_concurrent_refresh_has_one_successor_and_bounded_retry(mcp_client: TestClient) -> None:
    identity, code = grant(mcp_client)
    issued = exchange(mcp_client, identity, code).json()
    refresh = {
        "grant_type": "refresh_token",
        "client_id": identity,
        "refresh_token": issued["refresh_token"],
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: mcp_client.post("/oauth/token", data=refresh), range(2))
        )
    assert [r.status_code for r in responses] == [200, 200]
    first, second = (r.json() for r in responses)
    assert first["access_token"] == second["access_token"]
    assert first["refresh_token"] == second["refresh_token"]
    engine = build_engine(config().database_url)
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT id, kind, payload, expires_at FROM oauth_records")
        ).all()
        assert all(
            secret not in str(stored)
            for secret in [
                issued["refresh_token"],
                first["refresh_token"],
                first["access_token"],
                PASSWORD,
            ]
        )
        before = {r.id: r.expires_at for r in stored if r.kind in ("access", "refresh")}
    with TestClient(create_app(config()), base_url=ORIGIN) as restarted:
        repeated = restarted.post("/oauth/token", data=refresh)
        assert repeated.status_code == 200
        assert repeated.json()["refresh_token"] == first["refresh_token"]
    for changes in [
        {"client_id": "wrong-client"},
        {"resource": "https://other.example/mcp"},
    ]:
        assert mcp_client.post("/oauth/token", data={**refresh, **changes}).status_code == 400
    with engine.begin() as connection:
        after = connection.execute(
            text("SELECT id, expires_at FROM oauth_records WHERE kind IN ('access','refresh')")
        ).all()
        assert {r.id: r.expires_at for r in after} == before
        connection.execute(
            text(
                "UPDATE oauth_records SET expires_at=now()-interval '1 second' "
                "WHERE kind='refresh_retry'"
            )
        )
    assert mcp_client.post("/oauth/token", data=refresh).status_code == 400
    refresh["refresh_token"] = first["refresh_token"]
    assert mcp_client.post("/oauth/token", data=refresh).status_code == 200
    engine.dispose()


@pytest.mark.parametrize("revoke_original", [False, True])
def test_refresh_retry_does_not_restore_revoked_tokens(
    mcp_client: TestClient,
    revoke_original: bool,
) -> None:
    identity, code = grant(mcp_client)
    issued = exchange(mcp_client, identity, code).json()
    refresh = {
        "grant_type": "refresh_token",
        "client_id": identity,
        "refresh_token": issued["refresh_token"],
    }
    renewed = mcp_client.post("/oauth/token", data=refresh).json()
    token = issued["refresh_token"] if revoke_original else renewed["refresh_token"]
    assert (
        mcp_client.post("/oauth/revoke", data={"token": token, "client_id": identity}).status_code
        == 200
    )
    assert mcp_client.post("/oauth/token", data=refresh).status_code == 400


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
    assert len(tools) >= 32
    tool_names = {tool["name"] for tool in tools}
    assert {
        "create_account",
        "update_account",
        "financial_rebase_preview",
        "financial_rebase_commit",
        "list_financial_rebases",
    } <= tool_names
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
    configured = rpc(
        mcp_client,
        token,
        "tools/call",
        {
            "name": "setup_recurrence",
            "arguments": {
                "body": {
                    "description": "Fictional bill",
                    "expected_amount": "50.00",
                    "account_id": account,
                    "category_id": category,
                    "due_day": 5,
                    "start_date": "2026-10-01",
                    "transaction_ids": [transaction],
                }
            },
        },
    ).json()["result"]
    assert not configured.get("isError")
    rule_id = configured["structuredContent"]["recurrence"]["id"]
    generated = rpc(
        mcp_client,
        token,
        "tools/call",
        {
            "name": "generate_recurrence",
            "arguments": {
                "identity": rule_id,
                "body": {"start_date": "2026-10-01", "end_date": "2026-11-30"},
            },
        },
    ).json()["result"]
    assert not generated.get("isError")
    assert generated["structuredContent"]["created_count"] == 1


def test_mcp_updates_existing_recurrence(mcp_client: TestClient) -> None:
    identity, code = grant(mcp_client)
    token = exchange(mcp_client, identity, code).json()["access_token"]
    headers = {"Authorization": "Bearer test-token"}
    account = mcp_client.post(
        "/api/v1/accounts", headers=headers, json={"name": "Rule wallet"}
    ).json()["id"]
    rule = mcp_client.post(
        "/api/v1/recurrences",
        headers=headers,
        json={
            "description": "Monthly rule",
            "account_id": account,
            "expected_amount": "50",
            "due_day": 27,
            "start_date": "2026-09-01",
            "end_date": "2026-12-31",
        },
    ).json()
    arguments = {"identity": rule["id"], "body": {"end_date": "2028-12-31"}}
    for _ in range(2):
        result = rpc(
            mcp_client,
            token,
            "tools/call",
            {
                "name": "update_recurrence",
                "arguments": arguments,
            },
        ).json()["result"]
        assert not result.get("isError")
        assert result["structuredContent"]["id"] == rule["id"]
        assert result["structuredContent"]["end_date"] == "2028-12-31"
    assert len(mcp_client.get("/api/v1/recurrences", headers=headers).json()) == 1
    assert mcp_client.get("/api/v1/transactions", headers=headers).json() == []


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
    assert (
        mcp_client.get(
            "/api/v1/transactions", headers={"Authorization": "Bearer test-token"}
        ).json()
        == transactions_before
    )
    assert (
        mcp_client.get(
            "/api/v1/audit/transactions", headers={"Authorization": "Bearer test-token"}
        ).json()
        == history_before
    )


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
    assert (
        rpc(
            mcp_client,
            "test-token",
            "tools/call",
            {"name": "data_quality_dashboard", "arguments": {}},
        ).status_code
        == 401
    )


def test_mcp_financial_snapshot_and_comparison_are_authenticated_and_read_only(
    mcp_client: TestClient,
) -> None:
    identity, code = grant(mcp_client)
    token = exchange(mcp_client, identity, code).json()["access_token"]
    tools = rpc(mcp_client, token, "tools/list", {}).json()["result"]["tools"]
    names = {"financial_audit_snapshot", "compare_financial_audit"}
    for name in names:
        tool = next(item for item in tools if item["name"] == name)
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["_meta"]["securitySchemes"][0]["type"] == "oauth2"

    headers = {"Authorization": "Bearer test-token"}
    account = mcp_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Conta MCP de teste", "opening_balance": "100"},
    )
    assert account.status_code == 201, account.text
    before_accounts = mcp_client.get("/api/v1/accounts", headers=headers).json()
    before_transactions = mcp_client.get("/api/v1/transactions", headers=headers).json()
    before_history = mcp_client.get("/api/v1/audit/transactions", headers=headers).json()

    snapshot = rpc(
        mcp_client,
        token,
        "tools/call",
        {"name": "financial_audit_snapshot", "arguments": {}},
    ).json()["result"]
    assert not snapshot.get("isError"), snapshot
    baseline = snapshot["structuredContent"]
    assert baseline["read_only"] is True
    assert baseline["sections"]["accounts"]["count"] == 1
    assert len(baseline["snapshot_hash"]) == 64

    compared = rpc(
        mcp_client,
        token,
        "tools/call",
        {"name": "compare_financial_audit", "arguments": {"baseline": baseline}},
    ).json()["result"]
    assert not compared.get("isError"), compared
    assert compared["structuredContent"]["status"] == "MATCH"
    assert compared["structuredContent"]["read_only"] is True

    assert mcp_client.get("/api/v1/accounts", headers=headers).json() == before_accounts
    assert mcp_client.get("/api/v1/transactions", headers=headers).json() == before_transactions
    assert mcp_client.get("/api/v1/audit/transactions", headers=headers).json() == before_history
    assert (
        rpc(
            mcp_client,
            "invalid",
            "tools/call",
            {"name": "financial_audit_snapshot", "arguments": {}},
        ).status_code
        == 401
    )


def test_mcp_financial_quality_is_authenticated_and_non_mutating(
    mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity, code = grant(mcp_client)
    token = exchange(mcp_client, identity, code).json()["access_token"]
    tools = rpc(mcp_client, token, "tools/list", {}).json()["result"]["tools"]
    tool = next(item for item in tools if item["name"] == "financial_quality_dashboard")
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["annotations"]["destructiveHint"] is False
    assert tool["_meta"]["securitySchemes"][0]["type"] == "oauth2"

    headers = {"Authorization": "Bearer test-token"}
    account_response = mcp_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Não Essenciais", "opening_balance": "10.00"},
    )
    assert account_response.status_code == 201
    account_id = account_response.json()["id"]
    transaction_response = mcp_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "type": "EXPENSE",
            "status": "POSTED",
            "description": "Cinema",
            "amount": "20.00",
            "transaction_date": "2026-11-22",
            "account_id": account_id,
            "idempotency_key": "pr57-test-negative-envelope",
        },
    )
    assert transaction_response.status_code == 201, transaction_response.text

    before_accounts = mcp_client.get("/api/v1/accounts", headers=headers).json()
    before_transactions = mcp_client.get("/api/v1/transactions", headers=headers).json()
    before_audit = mcp_client.get("/api/v1/audit/transactions", headers=headers).json()

    original = httpx.AsyncClient.request
    requests: list[tuple[str, str, dict[str, str]]] = []

    async def observe(
        self: httpx.AsyncClient, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        if url == "/api/v1/dashboard/financial-quality":
            requests.append((method, url, dict(kwargs.get("params") or {})))
        return await original(self, method, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "request", observe)
    arguments = {"year": 2026, "month": 11, "as_of": "2026-11-22", "limit": 7}
    response = rpc(
        mcp_client,
        token,
        "tools/call",
        {"name": "financial_quality_dashboard", "arguments": arguments},
    )
    assert response.status_code == 200, response.text
    payload = response.json()["result"]
    assert not payload.get("isError"), payload
    report = payload["structuredContent"]
    assert report["read_only"] is True
    assert report["integrity_status"] != "CRITICAL"
    assert report["risk_status"] == "REVIEW"
    assert any(
        alert["code"] == "NEGATIVE_ENVELOPE_BALANCE"
        for alert in report["financial_alerts"]
    )
    assert requests == [
        (
            "GET",
            "/api/v1/dashboard/financial-quality",
            {"year": "2026", "month": "11", "as_of": "2026-11-22", "limit": "7"},
        )
    ]
    assert mcp_client.get("/api/v1/accounts", headers=headers).json() == before_accounts
    assert mcp_client.get("/api/v1/transactions", headers=headers).json() == before_transactions
    assert mcp_client.get("/api/v1/audit/transactions", headers=headers).json() == before_audit
    assert (
        rpc(
            mcp_client,
            "invalid",
            "tools/call",
            {"name": "financial_quality_dashboard", "arguments": arguments},
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"year": 1999, "month": 11},
        {"year": 2101, "month": 11},
        {"year": 2026, "month": 0},
        {"year": 2026, "month": 13},
        {"year": 2026, "month": 11, "limit": 0},
        {"year": 2026, "month": 11, "limit": 501},
        {"year": 2026, "month": 11, "as_of": "not-a-date"},
    ],
)
def test_mcp_financial_quality_rejects_invalid_inputs(
    mcp_client: TestClient, arguments: dict[str, Any]
) -> None:
    identity, code = grant(mcp_client)
    token = exchange(mcp_client, identity, code).json()["access_token"]
    response = rpc(
        mcp_client,
        token,
        "tools/call",
        {"name": "financial_quality_dashboard", "arguments": arguments},
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"].get("isError") is True
