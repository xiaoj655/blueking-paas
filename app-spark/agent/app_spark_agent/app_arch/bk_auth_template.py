# TencentBlueKing is pleased to support the open source community by making
# 蓝鲸智云 - PaaS 平台 (BlueKing - PaaS System) available.
# Copyright (C) Tencent. All rights reserved.
# Licensed under the MIT License (the "License"); you may not use this file except
# in compliance with the License. You may obtain a copy of the License at
#
#     http://opensource.org/licenses/MIT
#
# Unless required by applicable law or agreed to in writing, software distributed under
# the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
# either express or implied. See the License for the specific language governing permissions and
# limitations under the License.
#
# We undertake not to change the open source license (MIT license) applicable
# to the current version of the project delivered to anyone in the future.

"""Platform-managed BlueKing login middleware and dependencies for AppSpark FastAPI apps.

This file is synchronized by AppSpark before every launch; do not edit it by hand.
Call ``install_bk_auth(app)`` once in ``main.py`` after creating ``app = FastAPI()``,
and declare ``user: BkUser = Depends(get_current_user)`` in route handlers that need
the signed-in user.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx2
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

USERINFO_PATH = "/api/accounts/userinfo/"
DEFAULT_EXEMPT_PATHS: frozenset[str] = frozenset({"/healthz", "/favicon.ico"})
DEFAULT_EXEMPT_PREFIXES: tuple[str, ...] = ("/static/",)

_ACCESS_PERMISSION_DENIED_CODE = 1302403
_CACHE_TTL_SECONDS = 300.0
_CACHE_MAX_SIZE = 1024
_HTTP_TIMEOUT = httpx2.Timeout(5.0, connect=3.0, pool=3.0)
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:[\]]+$")


@dataclass(frozen=True, slots=True)
class BkUser:
    """Authenticated BlueKing user (or sandbox preview user)."""

    username: str
    display_name: str
    tenant_id: str | None = None
    time_zone: str | None = None
    is_preview: bool = False

    def to_userinfo_dict(self) -> dict[str, Any]:
        """Serialize into the standard ``/api/accounts/userinfo/`` response payload."""
        return {
            "authenticated": True,
            "username": self.username,
            "display_name": self.display_name,
            "tenant_id": self.tenant_id,
            "is_preview": self.is_preview,
        }


class _AuthConfigError(Exception):
    """Fail-closed configuration error."""


class _UpstreamAuthError(Exception):
    """Verification service unreachable, timed out, or returned an unexpected error."""


class _UnauthenticatedError(Exception):
    """Missing, expired, or invalid login credential."""


class _AccessDeniedError(Exception):
    """User is authenticated but lacks permission to access this application."""


@dataclass(frozen=True, slots=True)
class _AuthConfig:
    mode: str  # "production" | "preview" | "unconfigured"
    app_id: str = ""
    app_secret: str = ""
    multi_tenant: bool = False
    login_url: str = ""
    ticket_verify_url: str = ""
    token_apigw_url: str = ""
    token_is_login_url: str = ""
    preview_username: str = "preview-user"
    preview_display_name: str = "Preview User"
    config_error: str = ""

    @property
    def can_verify(self) -> bool:
        return self.mode == "production" and not self.config_error

    def preview_user(self) -> BkUser:
        return BkUser(username=self.preview_username, display_name=self.preview_display_name, is_preview=True)


def _validate_http_url(raw: str) -> str:
    value = raw.strip()
    if not value or "\x00" in value or any(ch.isspace() for ch in value):
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return value


def _build_apigw_userinfo_url(api_url_tmpl: str) -> str:
    tmpl = api_url_tmpl.strip()
    if not tmpl:
        return ""
    try:
        base = tmpl.format(api_name="bk-login").rstrip("/")
    except KeyError, IndexError, ValueError:
        return ""
    return _validate_http_url(f"{base}/prod/login/api/v3/open/bk-tokens/userinfo/")


def _resolve_url_env(env: Mapping[str, str], *keys: str) -> tuple[str, str]:
    """Return ``(validated_url, error_message)`` for the first non-empty env var in ``keys``."""
    for key in keys:
        raw = env.get(key, "").strip()
        if raw:
            validated = _validate_http_url(raw)
            if not validated:
                return "", f"{key} is not a valid HTTP(S) URL"
            return validated, ""
    return "", ""


def _load_auth_config(environ: Mapping[str, str] | None = None) -> _AuthConfig:
    env = os.environ if environ is None else environ
    app_id = env.get("BKPAAS_APP_ID", "").strip()

    # Production always takes strict precedence over preview mode (Fail-Closed).
    if not app_id:
        if env.get("APP_SPARK_PREVIEW_MODE", "").strip() == "1":
            return _AuthConfig(
                mode="preview",
                preview_username=env.get("APP_SPARK_PREVIEW_USER", "").strip() or "preview-user",
                preview_display_name=env.get("APP_SPARK_PREVIEW_DISPLAY_NAME", "").strip() or "Preview User",
            )
        return _AuthConfig(
            mode="unconfigured",
            config_error="Neither BKPAAS_APP_ID nor APP_SPARK_PREVIEW_MODE=1 is set",
        )

    multi_tenant = env.get("BKPAAS_MULTI_TENANT_MODE", "").strip().lower() == "true"
    login_url, login_err = _resolve_url_env(env, "BKPAAS_LOGIN_URL", "BK_LOGIN_URL")
    ticket_verify_url, ticket_err = _resolve_url_env(env, "BKPAAS_LOGIN_TICKET_VERIFY_URL")

    token_apigw_url, apigw_err = _resolve_url_env(env, "BKPAAS_LOGIN_USERINFO_APIGW_URL")
    if not token_apigw_url and multi_tenant:
        token_apigw_url = _build_apigw_userinfo_url(env.get("BK_API_URL_TMPL", ""))

    token_is_login_url, is_login_err = _resolve_url_env(env, "BKPAAS_LOGIN_IS_LOGIN_URL")
    if not token_is_login_url and login_url:
        token_is_login_url = _validate_http_url(f"{login_url.rstrip('/')}/api/v3/is_login/")

    return _AuthConfig(
        mode="production",
        app_id=app_id,
        app_secret=env.get("BKPAAS_APP_SECRET", "").strip(),
        multi_tenant=multi_tenant,
        login_url=login_url,
        ticket_verify_url=ticket_verify_url,
        token_apigw_url=token_apigw_url,
        token_is_login_url=token_is_login_url,
        config_error=login_err or ticket_err or apigw_err or is_login_err,
    )


class _BoundedTTLCache:
    """In-memory bounded LRU + TTL cache keyed by credential SHA-256 digests."""

    def __init__(self, *, max_size: int, ttl_seconds: float) -> None:
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, tuple[BkUser, float]] = OrderedDict()

    @staticmethod
    def make_key(kind: str, credential: str) -> str:
        return hashlib.sha256(f"{kind}:{credential}".encode()).hexdigest()

    def get(self, key: str) -> BkUser | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        user, expires_at = entry
        if time.monotonic() >= expires_at:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return user

    def set(self, key: str, user: BkUser) -> None:
        self._items[key] = (user, time.monotonic() + self._ttl_seconds)
        self._items.move_to_end(key)
        while len(self._items) > self._max_size:
            self._items.popitem(last=False)


class _BkTokenVerifier:
    """Async verifier for BlueKing ``bk_token`` (APIGW & ``is_login``) and ``bk_ticket``."""

    def __init__(
        self,
        config: _AuthConfig,
        *,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._cache = _BoundedTTLCache(max_size=_CACHE_MAX_SIZE, ttl_seconds=_CACHE_TTL_SECONDS)
        self._owns_client = http_client is None
        self._client = http_client

    def _get_client(self) -> httpx2.AsyncClient:
        if self._client is None:
            self._client = httpx2.AsyncClient(timeout=_HTTP_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def extract_credential(self, request: Request) -> tuple[str, str] | None:
        cfg = self.config
        token = self._read_credential(request, "bk_token", "x-user-bk-token")
        ticket = self._read_credential(request, "bk_ticket", "x-user-bk-ticket")

        if cfg.multi_tenant:
            return ("bk_token", token) if token else None

        # Prefer bk_ticket when both the ticket cookie and its verify URL exist.
        if ticket and cfg.ticket_verify_url:
            return ("bk_ticket", ticket)
        if token:
            return ("bk_token", token)
        if ticket:
            return ("bk_ticket", ticket)
        return None

    @staticmethod
    def _read_credential(request: Request, cookie_name: str, header_name: str) -> str:
        value = request.cookies.get(cookie_name, "").strip()
        if value:
            return value
        return request.headers.get(header_name, "").strip()

    async def verify(self, kind: str, credential: str) -> BkUser:
        cache_key = self._cache.make_key(kind, credential)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if kind == "bk_ticket":
            user = await self._verify_bk_ticket(credential)
        elif kind == "bk_token":
            user = await self._verify_bk_token(credential)
        else:
            raise _AuthConfigError(f"Unsupported credential kind: {kind}")

        self._cache.set(cache_key, user)
        return user

    async def _verify_bk_token(self, token: str) -> BkUser:
        cfg = self.config
        if not cfg.app_secret:
            logger.error("BKPAAS_APP_SECRET is missing; cannot verify bk_token")
            raise _AuthConfigError("BKPAAS_APP_SECRET is required to verify bk_token")

        if cfg.token_apigw_url:
            return await self._verify_bk_token_apigw(token, cfg.token_apigw_url)
        if cfg.multi_tenant:
            logger.error("BKPAAS_MULTI_TENANT_MODE=true requires a valid bk-login APIGW URL")
            raise _AuthConfigError("Missing bk-login APIGW URL in multi-tenant mode")
        if not cfg.token_is_login_url:
            logger.error("Neither bk-login APIGW URL nor BKPAAS_LOGIN_URL is configured for bk_token verification")
            raise _AuthConfigError("Missing bk_token verification URL")
        return await self._verify_bk_token_is_login(token, cfg.token_is_login_url)

    async def _verify_bk_token_apigw(self, token: str, url: str) -> BkUser:
        auth_header = json.dumps({"bk_app_code": self.config.app_id, "bk_app_secret": self.config.app_secret})
        resp = await self._http_get(
            url,
            params={"bk_token": token},
            headers={
                "X-Bkapi-Authorization": auth_header,
                "X-Bk-Tenant-Id": "default",
            },
        )
        if resp.status_code == 403:
            raise _AccessDeniedError("User does not have permission to access this application")
        if resp.status_code in {400, 401}:
            raise _UnauthenticatedError("Invalid or expired bk_token")
        if resp.status_code != 200:
            logger.error("bk-login APIGW verification returned unexpected status %s", resp.status_code)
            raise _UpstreamAuthError(f"bk-login APIGW returned HTTP {resp.status_code}")

        payload = self._parse_json_dict(resp, "bk-login APIGW")
        data, username = self._require_user_data(payload, "bk_username", "bk-login APIGW")
        display_name = data.get("display_name")
        tenant_id = data.get("tenant_id")
        time_zone = data.get("time_zone")
        return BkUser(
            username=username,
            display_name=display_name.strip() if isinstance(display_name, str) and display_name.strip() else username,
            tenant_id=tenant_id if isinstance(tenant_id, str) and tenant_id else None,
            time_zone=time_zone if isinstance(time_zone, str) and time_zone else None,
        )

    async def _verify_bk_token_is_login(self, token: str, url: str) -> BkUser:
        auth_header = json.dumps(
            {
                "bk_token": token,
                "bk_app_code": self.config.app_id,
                "bk_app_secret": self.config.app_secret,
            }
        )
        resp = await self._http_get(
            url,
            params={"bk_token": token},
            headers={"X-Bkapi-Authorization": auth_header},
        )
        self._raise_for_upstream_status(resp, "bk-login is_login")

        payload = self._parse_json_dict(resp, "bk-login is_login")
        code = payload.get("code")
        if code == 0:
            _, username = self._require_user_data(payload, "bk_username", "bk-login is_login")
            return BkUser(username=username, display_name=username)
        if code == _ACCESS_PERMISSION_DENIED_CODE:
            raise _AccessDeniedError("User does not have permission to access this application")
        raise _UnauthenticatedError("Invalid or expired bk_token")

    async def _verify_bk_ticket(self, ticket: str) -> BkUser:
        url = self.config.ticket_verify_url
        if not url:
            logger.error("Received bk_ticket credential but BKPAAS_LOGIN_TICKET_VERIFY_URL is not configured")
            raise _AuthConfigError("BKPAAS_LOGIN_TICKET_VERIFY_URL is not configured")

        resp = await self._http_get(url, params={"bk_ticket": ticket})
        self._raise_for_upstream_status(resp, "bk_ticket verify")

        payload = self._parse_json_dict(resp, "bk_ticket verify")
        if payload.get("ret") != 0:
            raise _UnauthenticatedError("Invalid or expired bk_ticket")
        _, username = self._require_user_data(payload, "username", "bk_ticket verify")
        return BkUser(username=username, display_name=username)

    async def _http_get(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> httpx2.Response:
        try:
            return await self._get_client().get(url, params=params, headers=headers)
        except httpx2.HTTPError as exc:
            # Omit traceback so httpx error details cannot leak query-string tokens/tickets into logs.
            logger.error("BlueKing login verification HTTP request failed: %s", type(exc).__name__)  # noqa: TRY400
            raise _UpstreamAuthError("BlueKing login verification request failed") from exc

    @staticmethod
    def _raise_for_upstream_status(resp: httpx2.Response, label: str) -> None:
        """Treat statuses that say nothing about the credential as verification service failures."""
        if resp.status_code >= 500 or resp.status_code in {404, 429}:
            logger.error("%s returned HTTP %s", label, resp.status_code)
            raise _UpstreamAuthError(f"{label} returned HTTP {resp.status_code}")

    @staticmethod
    def _parse_json_dict(resp: httpx2.Response, label: str) -> dict[str, Any]:
        try:
            payload = resp.json()
        except ValueError as exc:
            logger.error("%s returned non-JSON payload (HTTP %s)", label, resp.status_code)  # noqa: TRY400
            raise _UpstreamAuthError(f"{label} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            logger.error("%s returned unexpected JSON type %s", label, type(payload).__name__)
            raise _UpstreamAuthError(f"{label} returned non-object JSON")
        return payload

    @staticmethod
    def _require_user_data(payload: dict[str, Any], username_key: str, label: str) -> tuple[dict[str, Any], str]:
        """Return the payload's data object and its stripped, non-empty username."""
        data = payload.get("data")
        if not isinstance(data, dict):
            raise _UpstreamAuthError(f"{label} response missing data object")
        username = data.get(username_key)
        if not isinstance(username, str) or not username.strip():
            raise _UpstreamAuthError(f"{label} response missing {username_key}")
        return data, username.strip()


def _is_api_request(request: Request) -> bool:
    path = request.url.path
    if path == "/api" or path.startswith("/api/"):
        return True
    if request.method.upper() not in {"GET", "HEAD"}:
        return True
    if request.headers.get("x-requested-with", "").strip().lower() == "xmlhttprequest":
        return True
    accept = request.headers.get("accept", "").lower()
    return "application/json" in accept and "text/html" not in accept


def _build_external_url(request: Request) -> str:
    raw_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    scheme = raw_proto if raw_proto in {"http", "https"} else request.url.scheme

    raw_host = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
    host = raw_host if raw_host and _SAFE_HOST_RE.fullmatch(raw_host) else request.url.netloc

    path = "/" + request.url.path.lstrip("/")
    return urlunsplit((scheme, host, path, request.url.query, ""))


def _build_login_redirect_url(base_login_url: str, c_url: str) -> str:
    parsed = urlsplit(base_login_url)
    params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "c_url"]
    params.append(("c_url", c_url))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), parsed.fragment))


def _iframe_login_response(login_redirect_url: str) -> HTMLResponse:
    js_url = json.dumps(login_redirect_url).replace("</", "<\\/")
    href = html.escape(login_redirect_url, quote=True)
    body = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Sign in required</title></head>"
        "<body><p>Redirecting to BlueKing sign-in...</p>"
        f"<p><a href='{href}' target='_top' rel='noopener noreferrer'>Click here to sign in</a></p>"
        f"<script>(function(){{var u={js_url};try{{window.top.location.href=u;}}catch(e){{}}}})();</script>"
        "</body></html>"
    )
    return HTMLResponse(content=body, status_code=401)


class _BkAuthMiddleware:
    """Pure ASGI middleware protecting routes with BlueKing login."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        verifier: _BkTokenVerifier,
        custom_exempt_paths: frozenset[str],
        custom_exempt_prefixes: tuple[str, ...],
    ) -> None:
        self.app = app
        self._verifier = verifier
        self._custom_exempt_paths = custom_exempt_paths
        self._custom_exempt_prefixes = custom_exempt_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            try:
                await self.app(scope, receive, send)
            finally:
                await self._verifier.aclose()
            return

        path: str = scope.get("path", "")
        if scope["type"] != "http" or path in DEFAULT_EXEMPT_PATHS or path.startswith(DEFAULT_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        state = scope.setdefault("state", {})
        cfg = self._verifier.config

        if cfg.mode == "preview":
            state["bk_user"] = cfg.preview_user()
        elif path in self._custom_exempt_paths or path.startswith(self._custom_exempt_prefixes):
            await self._attach_optional_user(request, state)
        else:
            auth_response = await self._authenticate_request(request, state)
            if auth_response is not None:
                await auth_response(scope, receive, send)
                return

        await self.app(scope, receive, send)

    async def _attach_optional_user(self, request: Request, state: dict[str, Any]) -> None:
        if not self._verifier.config.can_verify:
            return
        cred = self._verifier.extract_credential(request)
        if cred is None:
            return
        try:
            state["bk_user"] = await self._verifier.verify(*cred)
        except _AuthConfigError, _UpstreamAuthError, _UnauthenticatedError, _AccessDeniedError:
            return

    async def _authenticate_request(self, request: Request, state: dict[str, Any]) -> Response | None:
        cfg = self._verifier.config
        if not cfg.can_verify:
            logger.error("BlueKing authentication unavailable: %s", cfg.config_error or "invalid mode")
            return JSONResponse(status_code=503, content={"detail": "Authentication service is not configured"})

        cred = self._verifier.extract_credential(request)
        if cred is None:
            return self._unauthenticated_response(request)

        try:
            state["bk_user"] = await self._verifier.verify(*cred)
        except _UnauthenticatedError:
            return self._unauthenticated_response(request)
        except _AccessDeniedError:
            return JSONResponse(
                status_code=403,
                content={"detail": "You do not have permission to access this application"},
            )
        except _AuthConfigError, _UpstreamAuthError:
            return JSONResponse(
                status_code=503,
                content={"detail": "BlueKing authentication service is temporarily unavailable"},
            )
        return None

    def _unauthenticated_response(self, request: Request) -> Response:
        login_url = self._verifier.config.login_url
        if not login_url:
            logger.error("BKPAAS_LOGIN_URL is missing or invalid; cannot build login response")
            return JSONResponse(
                status_code=503,
                content={"detail": "BlueKing login URL is not configured"},
            )
        if _is_api_request(request):
            return JSONResponse(
                status_code=401,
                content={"authenticated": False, "login_url": login_url},
            )
        redirect_url = _build_login_redirect_url(login_url, _build_external_url(request))
        if request.headers.get("sec-fetch-dest", "").strip().lower() == "iframe":
            return _iframe_login_response(redirect_url)
        return RedirectResponse(url=redirect_url, status_code=302)


def get_current_user(request: Request) -> BkUser:
    """FastAPI dependency returning the authenticated user or raising HTTP 401."""
    user = getattr(request.state, "bk_user", None)
    if not isinstance(user, BkUser):
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def get_optional_user(request: Request) -> BkUser | None:
    """FastAPI dependency returning the authenticated user when present, or ``None``."""
    user = getattr(request.state, "bk_user", None)
    return user if isinstance(user, BkUser) else None


def install_bk_auth(
    app: FastAPI,
    *,
    exempt_paths: Sequence[str] = (),
    exempt_prefixes: Sequence[str] = (),
    http_client: httpx2.AsyncClient | None = None,
) -> None:
    """Install BlueKing login protection and ``GET /api/accounts/userinfo/`` onto ``app``."""
    if getattr(app.state, "_bk_auth_installed", False):
        return
    app.state._bk_auth_installed = True

    config = _load_auth_config()
    verifier = _BkTokenVerifier(config, http_client=http_client)
    app.add_middleware(
        _BkAuthMiddleware,
        verifier=verifier,
        custom_exempt_paths=frozenset(exempt_paths),
        custom_exempt_prefixes=tuple(exempt_prefixes),
    )

    @app.get(USERINFO_PATH)
    async def _bk_auth_userinfo(request: Request) -> dict[str, Any]:
        return get_current_user(request).to_userinfo_dict()
