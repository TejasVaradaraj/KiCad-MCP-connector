from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://www.googleapis.com/oauth2/v3/userinfo"

def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Google token error {e.code}: {detail}") from e

def _get_json(url: str, bearer: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))

class GoogleOAuthProvider:
    """MCP authorization server that sends the user through Google, then issues MCP tokens."""

    def __init__(
        self,
        *,
        google_client_id: str,
        google_client_secret: str,
        public_base_url: str,
        allowed_emails: list[str] | None = None,
    ) -> None:
        self.google_client_id = google_client_id
        self.google_client_secret = google_client_secret
        self.public_base_url = public_base_url.rstrip("/")
        self.allowed_emails = {e.strip().lower() for e in (allowed_emails or []) if e.strip()}
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._pending_google: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams]] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access: dict[str, AccessToken] = {}
        self._refresh: dict[str, RefreshToken] = {}

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.public_base_url}/oauth/callback"

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        google_state = secrets.token_urlsafe(24)
        self._pending_google[google_state] = (client, params)
        query = urllib.parse.urlencode(
            {
                "client_id": self.google_client_id,
                "redirect_uri": self.google_redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": google_state,
                "access_type": "online",
                "prompt": "select_account",
            }
        )
        return f"{GOOGLE_AUTH}?{query}"

    async def handle_google_callback(self, request: Request) -> Response:
        err = request.query_params.get("error")
        if err:
            return HTMLResponse(f"<h1>Google login failed</h1><p>{err}</p>", status_code=400)
        code = request.query_params.get("code")
        google_state = request.query_params.get("state")
        if not code or not google_state:
            return HTMLResponse("<h1>Missing code or state</h1>", status_code=400)
        pending = self._pending_google.pop(google_state, None)
        if pending is None:
            return HTMLResponse("<h1>Unknown or expired login</h1>", status_code=400)
        client, params = pending
        try:
            tokens = _post_form(
                GOOGLE_TOKEN,
                {
                    "code": code,
                    "client_id": self.google_client_id,
                    "client_secret": self.google_client_secret,
                    "redirect_uri": self.google_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            access = tokens.get("access_token")
            if not access:
                raise RuntimeError("Google did not return an access token")
            info = _get_json(GOOGLE_USERINFO, str(access))
        except Exception as e:
            return HTMLResponse(f"<h1>Google token exchange failed</h1><p>{e}</p>", status_code=400)

        email = str(info.get("email") or "").strip().lower()
        if not email or not info.get("email_verified", True):
            return HTMLResponse("<h1>Google account has no verified email</h1>", status_code=403)
        if self.allowed_emails and email not in self.allowed_emails:
            return HTMLResponse(
                f"<h1>Not allowed</h1><p>{email} is not in ALLOWED_GOOGLE_EMAILS.</p>",
                status_code=403,
            )

        mcp_code = secrets.token_urlsafe(32)
        self._auth_codes[mcp_code] = AuthorizationCode(
            code=mcp_code,
            scopes=params.scopes or ["kicad"],
            expires_at=time.time() + 300,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=email,
        )
        redirect = construct_redirect_uri(
            str(params.redirect_uri),
            code=mcp_code,
            state=params.state,
        )
        return RedirectResponse(url=redirect, status_code=302)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        item = self._auth_codes.get(authorization_code)
        if item is None or item.client_id != client.client_id:
            return None
        if item.expires_at < time.time():
            self._auth_codes.pop(authorization_code, None)
            return None
        return item

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        stored = self._auth_codes.pop(authorization_code.code, None)
        if stored is None or stored.client_id != client.client_id:
            raise TokenError(error="invalid_grant", error_description="Unknown authorization code")
        return self._issue(client, stored.scopes, stored.subject, stored.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        item = self._refresh.get(refresh_token)
        if item is None or item.client_id != client.client_id:
            return None
        if item.expires_at is not None and item.expires_at < time.time():
            self._refresh.pop(refresh_token, None)
            return None
        return item

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        stored = self._refresh.pop(refresh_token.token, None)
        if stored is None or stored.client_id != client.client_id:
            raise TokenError(error="invalid_grant", error_description="Unknown refresh token")
        granted = scopes or stored.scopes
        return self._issue(client, granted, stored.subject, None)

    async def load_access_token(self, token: str) -> AccessToken | None:
        item = self._access.get(token)
        if item is None:
            return None
        if item.expires_at is not None and item.expires_at < time.time():
            self._access.pop(token, None)
            return None
        return item

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self._access.pop(token.token, None)
        else:
            self._refresh.pop(token.token, None)

    async def exchange_identity_assertion(self, client: OAuthClientInformationFull, params: Any) -> OAuthToken:
        raise TokenError(
            error="unsupported_grant_type",
            error_description="Identity assertion is not supported",
        )

    def _issue(
        self,
        client: OAuthClientInformationFull,
        scopes: list[str],
        subject: str | None,
        resource: str | None,
    ) -> OAuthToken:
        access_raw = secrets.token_urlsafe(32)
        refresh_raw = secrets.token_urlsafe(32)
        now = int(time.time())
        access = AccessToken(
            token=access_raw,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=now + 3600,
            resource=resource,
            subject=subject,
        )
        refresh = RefreshToken(
            token=refresh_raw,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=now + 86400 * 7,
            subject=subject,
        )
        self._access[access_raw] = access
        self._refresh[refresh_raw] = refresh
        return OAuthToken(
            access_token=access_raw,
            token_type="Bearer",
            expires_in=3600,
            scope=" ".join(scopes),
            refresh_token=refresh_raw,
        )
