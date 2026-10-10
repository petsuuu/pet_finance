"""Single-owner OAuth 2.1: PKCE, exact callbacks, consent, hashed opaque tokens.

The login credential is entered only on our HTTPS consent page. It never becomes
an MCP bearer token. Grants live in PostgreSQL so workers/redeploys share state.
"""

import base64
import hashlib
import hmac
import html
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from mcp.server.auth.provider import AccessToken
from pydantic import BaseModel, Field
from sqlalchemy import Connection, Engine, text

from app.core.config import Settings


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def allowed_redirect(value: str) -> bool:
    url = urlsplit(value)
    return bool(
        url.scheme == "https"
        and url.netloc == "chatgpt.com"
        and not url.query
        and not url.fragment
        and (
            url.path == "/connector_platform_oauth_redirect"
            or re.fullmatch(r"/connector/oauth/[A-Za-z0-9_-]+", url.path)
        )
    )


class ClientRegistration(BaseModel):
    redirect_uris: list[str] = Field(min_length=1, max_length=5)
    client_name: str = Field(default="ChatGPT", max_length=120)
    token_endpoint_auth_method: Literal["none"] = "none"
    grant_types: list[Literal["authorization_code", "refresh_token"]] = [
        "authorization_code",
        "refresh_token",
    ]
    response_types: list[Literal["code"]] = ["code"]


class OwnerOAuth:
    def __init__(self, settings: Settings, engine: Engine):
        assert settings.mcp_public_url and settings.mcp_login_password
        self.settings = settings
        self.engine = engine
        self.issuer = settings.mcp_public_url
        self.resource = self.issuer + "/mcp"
        self.password = settings.mcp_login_password.get_secret_value()
        self.binding = digest(self.password)

    def save(
        self,
        connection: Connection,
        kind: str,
        payload: dict[str, Any],
        seconds: int,
        token: str | None = None,
    ) -> str:
        token = token or secrets.token_urlsafe(48)
        connection.execute(
            text("""INSERT INTO oauth_records (id,user_id,kind,payload,expires_at)
                VALUES (:id,:user,:kind,CAST(:payload AS jsonb),:expires)"""),
            {
                "id": digest(token),
                "user": self.settings.user_id,
                "kind": kind,
                "payload": json.dumps({**payload, "binding": self.binding}),
                "expires": datetime.now(UTC) + timedelta(seconds=seconds),
            },
        )
        return token

    def load(
        self, connection: Connection, token: str, kind: str, lock: bool = False
    ) -> dict[str, Any] | None:
        query = """SELECT payload, expires_at FROM oauth_records
            WHERE id=:id AND user_id=:user AND kind=:kind AND expires_at>now()"""
        if lock:
            query += " FOR UPDATE"
        row = (
            connection.execute(
                text(query), {"id": digest(token), "user": self.settings.user_id, "kind": kind}
            )
            .mappings()
            .first()
        )
        if row is None or row["payload"].get("binding") != self.binding:
            return None
        return {**row["payload"], "expires": int(row["expires_at"].timestamp())}

    def delete(self, connection: Connection, token: str) -> None:
        connection.execute(
            text("DELETE FROM oauth_records WHERE id=:id AND user_id=:user"),
            {"id": digest(token), "user": self.settings.user_id},
        )

    def rate_limit(self, connection: Connection, kind: str, maximum: int) -> None:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"oauth:{self.settings.user_id}:{kind}"},
        )
        connection.execute(text("DELETE FROM oauth_records WHERE expires_at < now()"))
        count: int = connection.execute(
            text("SELECT count(*) FROM oauth_records WHERE user_id=:user AND kind=:kind"),
            {"user": self.settings.user_id, "kind": kind},
        ).scalar_one()
        if count >= maximum:
            raise HTTPException(429, "Too many attempts; retry in five minutes")
        self.save(connection, kind, {}, 300)

    async def verify_token(self, token: str) -> AccessToken | None:
        # MCP owns its request auth context; no REST API token is accepted here.
        from anyio import to_thread

        return await to_thread.run_sync(self.verify, token)

    def verify(self, token: str) -> AccessToken | None:
        with self.engine.connect() as connection:
            data = self.load(connection, token, "access")
        if data is None or data.get("resource") != self.resource:
            return None
        return AccessToken(
            token=token,
            client_id=data["client_id"],
            scopes=["finance"],
            expires_at=data["expires"],
            resource=self.resource,
            subject=str(self.settings.user_id),
        )

    def refresh_pair(self, credential: str) -> tuple[str, str]:
        # Reconstruct the same successor on a short retry without storing raw tokens.
        def derive(kind: str) -> str:
            value = hmac.digest(
                self.password.encode(),
                ("pet-refresh-v1:" + kind + ":" + credential).encode(),
                "sha384",
            )
            return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

        return derive("access"), derive("refresh")

    def tokens(
        self,
        connection: Connection,
        data: dict[str, Any],
        credential: str | None = None,
    ) -> JSONResponse:
        payload = {"client_id": data["client_id"], "resource": self.resource}
        pair = self.refresh_pair(credential) if credential else (None, None)
        access = self.save(connection, "access", payload, 3600, token=pair[0])
        refresh = self.save(connection, "refresh", payload, 30 * 86400, token=pair[1])
        return JSONResponse(
            {
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": refresh,
                "scope": "finance",
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
        def metadata() -> dict[str, Any]:
            return {
                "issuer": self.issuer,
                "authorization_endpoint": self.issuer + "/oauth/authorize",
                "token_endpoint": self.issuer + "/oauth/token",
                "registration_endpoint": self.issuer + "/oauth/register",
                "revocation_endpoint": self.issuer + "/oauth/revoke",
                "authorization_response_iss_parameter_supported": True,
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": ["finance"],
            }

        @router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
        @router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
        def resource_metadata() -> dict[str, Any]:
            return {
                "resource": self.resource,
                "authorization_servers": [self.issuer],
                "scopes_supported": ["finance"],
                "bearer_methods_supported": ["header"],
            }

        @router.post("/oauth/register", status_code=201, include_in_schema=False)
        def register(body: ClientRegistration) -> dict[str, Any]:
            if not all(allowed_redirect(uri) for uri in body.redirect_uris):
                raise HTTPException(400, "Only the exact ChatGPT OAuth callbacks are supported")
            with self.engine.begin() as connection:
                self.rate_limit(connection, "registration_attempt", 30)
                client_id = self.save(connection, "client", body.model_dump(), 365 * 86400)
            return {
                **body.model_dump(),
                "client_id": client_id,
                "client_id_issued_at": int(datetime.now(UTC).timestamp()),
            }

        @router.get("/oauth/authorize", include_in_schema=False)
        def authorize(
            client_id: str,
            redirect_uri: str,
            resource: str,
            code_challenge: str,
            code_challenge_method: str,
            response_type: str,
            state: str | None = None,
            scope: str = "finance",
        ) -> HTMLResponse:
            if (
                response_type != "code"
                or code_challenge_method != "S256"
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code_challenge)
                or resource != self.resource
                or scope != "finance"
            ):
                raise HTTPException(400, "Invalid authorization parameters")
            with self.engine.begin() as connection:
                client = self.load(connection, client_id, "client")
                if client is None or redirect_uri not in client["redirect_uris"]:
                    raise HTTPException(400, "Invalid client or redirect URI")
                self.rate_limit(connection, "authorization_attempt", 60)
                csrf = secrets.token_urlsafe(32)
                login = self.save(
                    connection,
                    "login",
                    {
                        "client_id": client_id,
                        "redirect_uri": redirect_uri,
                        "state": state,
                        "code_challenge": code_challenge,
                        "resource": resource,
                        "csrf": digest(csrf),
                    },
                    600,
                )
            response = HTMLResponse(
                f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8">
                <meta name="viewport" content="width=device-width,initial-scale=1">
                <title>Conectar Pet Finance</title><body>
                <h1>Conectar Pet Finance ao ChatGPT</h1>
                <p>Autorize consultar seus dados financeiros, criar lançamentos e atualizar
                pagamentos. Esta conexão não permite excluir lançamentos.</p>
                <p>Informe a senha de conexão configurada no Render (MCP_LOGIN_PASSWORD).</p>
                <form method="post" action="/oauth/approve">
                <input type="hidden" name="login" value="{html.escape(login, quote=True)}">
                <label>Senha de conexão <input name="password" type="password"
                required autocomplete="current-password"></label>
                <button type="submit">Autorizar conexão</button></form></body></html>""",
                headers={
                    "Cache-Control": "no-store",
                    "X-Frame-Options": "DENY",
                    "Content-Security-Policy": "default-src 'none'; form-action 'self' "
                    "https://chatgpt.com; "
                    "frame-ancestors 'none'; base-uri 'none'",
                    # HTML form POSTs under no-referrer send Origin: null.
                    # Preserve the same-origin proof without leaking the login query.
                    "Referrer-Policy": "strict-origin",
                    "X-Content-Type-Options": "nosniff",
                },
            )
            response.set_cookie(
                "pet_oauth_" + digest(login)[:16],
                csrf,
                secure=True,
                httponly=True,
                samesite="lax",
                max_age=600,
                path="/oauth",
            )
            return response

        @router.post("/oauth/approve", include_in_schema=False)
        def approve(
            request: Request,
            login: str = Form(),
            password: str = Form(),
        ) -> RedirectResponse:
            # Commit failed attempts as well: raising inside begin() would roll them back.
            with self.engine.begin() as connection:
                self.rate_limit(connection, "login_attempt", 20)
            with self.engine.begin() as connection:
                data = self.load(connection, login, "login", lock=True)
                cookie = request.cookies.get("pet_oauth_" + digest(login)[:16], "")
                if (
                    data is None
                    or not secrets.compare_digest(digest(cookie), data["csrf"])
                    or request.headers.get("origin") != self.issuer
                ):
                    raise HTTPException(400, "Invalid or expired login session")
                if not secrets.compare_digest(password.encode(), self.password.encode()):
                    raise HTTPException(401, "Invalid connection password")
                code = self.save(connection, "code", data, 120)
                self.delete(connection, login)
            query = {"code": code, "iss": self.issuer}
            if data["state"] is not None:
                query["state"] = data["state"]
            response = RedirectResponse(data["redirect_uri"] + "?" + urlencode(query), 303)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.delete_cookie("pet_oauth_" + digest(login)[:16], path="/oauth")
            return response

        @router.post("/oauth/token", include_in_schema=False)
        def token(
            grant_type: str = Form(),
            client_id: str = Form(),
            resource: str | None = Form(default=None),
            code: str = Form(default=""),
            code_verifier: str = Form(default=""),
            redirect_uri: str = Form(default=""),
            refresh_token: str = Form(default=""),
        ) -> JSONResponse:
            def error(name: str) -> JSONResponse:
                return JSONResponse({"error": name}, 400, headers={"Cache-Control": "no-store"})

            if grant_type not in ("authorization_code", "refresh_token"):
                return error("unsupported_grant_type")
            with self.engine.begin() as connection:
                if self.load(connection, client_id, "client") is None:
                    return error("invalid_client")
                kind = "code" if grant_type == "authorization_code" else "refresh"
                credential = code if kind == "code" else refresh_token
                data = self.load(connection, credential, kind, lock=True)
                if data is None and kind == "refresh":
                    retry = self.load(connection, credential, "refresh_retry", lock=True)
                    if (
                        retry is not None
                        and retry["client_id"] == client_id
                        and retry["resource"] == self.resource
                        and (resource is None or resource == self.resource)
                    ):
                        access, refresh = self.refresh_pair(credential)
                        access_data = self.load(connection, access, "access")
                        refresh_data = self.load(connection, refresh, "refresh")
                        if access_data is not None and refresh_data is not None:
                            return JSONResponse(
                                {
                                    "access_token": access,
                                    "token_type": "Bearer",
                                    "expires_in": max(
                                        0,
                                        access_data["expires"] - int(datetime.now(UTC).timestamp()),
                                    ),
                                    "refresh_token": refresh,
                                    "scope": "finance",
                                },
                                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
                            )
                    return error("invalid_grant")
                if (
                    data is None
                    or data["client_id"] != client_id
                    or data["resource"] != self.resource
                    # A refresh is already bound to its original resource. Some
                    # clients omit this optional indicator when renewing access.
                    or (resource is not None and resource != self.resource)
                    or (kind == "code" and resource is None)
                ):
                    return error("invalid_grant")
                if kind == "code":
                    challenge = (
                        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
                        .rstrip(b"=")
                        .decode()
                    )
                    if (
                        not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", code_verifier)
                        or redirect_uri != data["redirect_uri"]
                        or not secrets.compare_digest(challenge, data["code_challenge"])
                    ):
                        return error("invalid_grant")
                self.delete(connection, credential)
                response = self.tokens(connection, data, credential if kind == "refresh" else None)
                if kind == "refresh":
                    # Concurrent requests or a lost response get the same tokens for
                    # 30 seconds. Never issue another pair or extend their lifetime.
                    self.save(
                        connection,
                        "refresh_retry",
                        {
                            "client_id": client_id,
                            "resource": self.resource,
                        },
                        30,
                        token=credential,
                    )
                return response

        @router.post("/oauth/revoke", include_in_schema=False)
        def revoke(token: str = Form(), client_id: str = Form()) -> JSONResponse:
            with self.engine.begin() as connection:
                for kind in ("access", "refresh", "refresh_retry"):
                    data = self.load(connection, token, kind, lock=True)
                    if data and data["client_id"] == client_id:
                        if kind == "refresh_retry":
                            for successor in self.refresh_pair(token):
                                self.delete(connection, successor)
                        self.delete(connection, token)
            return JSONResponse({}, headers={"Cache-Control": "no-store"})

        return router
