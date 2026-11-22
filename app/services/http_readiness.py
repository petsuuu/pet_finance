"""Unauthenticated, non-mutating production HTTP/OAuth readiness probe.

Run manually in GitHub Actions; no financial or OAuth credentials are used.
This probe is not authenticated MCP acceptance or deployment authorization.
"""

import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ORIGIN = "https://pet-finance-qoyi.onrender.com"
TIMEOUT = 15


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def probe(
    path: str, *, method: str = "GET", body: bytes | None = None
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Connect exclusively to the pinned service, without following redirects."""
    if path not in {
        "/health",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource/mcp",
        "/mcp",
    }:
        raise ValueError("unapproved probe path")
    if method == "POST" and path != "/mcp":
        raise ValueError("POST allowed only for unauthenticated MCP denial test")
    req = Request(
        ORIGIN + path,
        data=body,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        response = build_opener(NoRedirect()).open(req, timeout=TIMEOUT)
    except HTTPError as exc:
        response = exc
    with response:
        status = response.status
        headers = {k.lower(): v for k, v in response.headers.items()}
        raw = response.read(32768)
    try:
        payload = json.loads(raw) if raw else {}
    except (ValueError, UnicodeDecodeError):
        payload = {}
    return status, payload if isinstance(payload, dict) else {}, headers


def assess(results: dict[str, tuple[int, dict[str, Any], dict[str, str]]]) -> list[str]:
    """Return failures; absence of a failure is not authenticated acceptance."""
    failures: list[str] = []
    health, auth, resource, denied = (
        results[key] for key in ("health", "auth", "resource", "denied")
    )
    if health[0] != 200 or health[1].get("status") != "ok":
        failures.append("health")
    metadata = auth[1]
    if (
        auth[0] != 200
        or metadata.get("issuer") != ORIGIN
        or "S256" not in metadata.get("code_challenge_methods_supported", [])
        or metadata.get("token_endpoint") != ORIGIN + "/oauth/token"
    ):
        failures.append("oauth_metadata")
    if (
        resource[0] != 200
        or resource[1].get("resource") != ORIGIN + "/mcp"
        or ORIGIN not in resource[1].get("authorization_servers", [])
    ):
        failures.append("oauth_resource")
    if denied[0] != 401 or "bearer" not in denied[2].get("www-authenticate", "").lower():
        failures.append("mcp_without_token_must_be_401")
    return failures


def main() -> int:
    try:
        results = {
            "health": probe("/health"),
            "auth": probe("/.well-known/oauth-authorization-server"),
            "resource": probe("/.well-known/oauth-protected-resource/mcp"),
            "denied": probe(
                "/mcp",
                method="POST",
                body=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',
            ),
        }
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        print(f"NO-GO: connection/readiness failure ({type(exc).__name__}); no credentials used")
        return 1
    for name, (status, _, _) in results.items():
        print(f"{name}: HTTP {status}")
    failures = assess(results)
    if failures:
        print("NO-GO: " + ", ".join(failures))
        return 1
    print("PASS: public HTTP/OAuth and unauthenticated MCP rejection only")
    print(
        "NOT VERIFIED: authenticated HTTP/MCP access, new MCP tools, financial integrity or deploy"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
