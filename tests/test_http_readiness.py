"""Offline tests for the public-only, no-credential HTTP preflight."""

from app.services.http_readiness import ORIGIN, assess, probe


def good_results() -> dict[str, tuple[int, dict, dict]]:
    return {
        "health": (200, {"status": "ok"}, {}),
        "auth": (
            200,
            {
                "issuer": ORIGIN,
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint": ORIGIN + "/oauth/token",
            },
            {},
        ),
        "resource": (
            200,
            {"resource": ORIGIN + "/mcp", "authorization_servers": [ORIGIN]},
            {},
        ),
        "denied": (
            401,
            {},
            {"www-authenticate": "Bearer"},
        ),
    }


def test_public_preflight_accepts_only_expected_metadata() -> None:
    assert assess(good_results()) == []


def test_public_preflight_fails_closed_on_wrong_resource_or_token_denial() -> None:
    results = good_results()
    results["resource"] = (200, {"resource": "https://wrong.example/mcp"}, {})
    results["denied"] = (200, {}, {})
    assert assess(results) == ["oauth_resource", "mcp_without_token_must_be_401"]


def test_public_preflight_requires_health_and_pkce() -> None:
    results = good_results()
    results["health"] = (503, {}, {})
    results["auth"] = (200, {"issuer": ORIGIN}, {})
    assert assess(results) == ["health", "oauth_metadata"]


def test_public_preflight_rejects_unapproved_paths_and_post_operations() -> None:
    import pytest

    with pytest.raises(ValueError, match="unapproved probe path"):
        probe("/api/v1/transactions")
    with pytest.raises(ValueError, match="POST allowed only"):
        probe("/health", method="POST")
