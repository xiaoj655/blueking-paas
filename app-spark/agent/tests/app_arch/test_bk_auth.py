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

"""Unit tests for the platform-synchronized ``bk_auth.py`` module."""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from collections.abc import Callable
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app_spark_agent.app_arch.bk_auth import BK_AUTH_FILENAME, sync_bk_auth
from app_spark_agent.app_arch.files import FileSyncStatus

_AUTH_ENV_KEYS = (
    "APP_SPARK_PREVIEW_MODE",
    "APP_SPARK_PREVIEW_USER",
    "APP_SPARK_PREVIEW_DISPLAY_NAME",
    "BKPAAS_APP_ID",
    "BKPAAS_APP_SECRET",
    "BKPAAS_MULTI_TENANT_MODE",
    "BKPAAS_LOGIN_URL",
    "BK_LOGIN_URL",
    "BK_API_URL_TMPL",
    "BKPAAS_LOGIN_TICKET_VERIFY_URL",
    "BKPAAS_LOGIN_USERINFO_APIGW_URL",
    "BKPAAS_LOGIN_IS_LOGIN_URL",
)


@pytest.fixture(autouse=True)
def clear_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with a clean auth environment."""
    for key in _AUTH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(scope="module")
def bk_auth_mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    """Load the exact standalone ``bk_auth.py`` synchronized into a workspace."""
    workspace = tmp_path_factory.mktemp("bk_auth_workspace")
    result = sync_bk_auth(workspace)
    assert result.status is FileSyncStatus.SYNCED
    module_path = workspace / BK_AUTH_FILENAME
    spec = importlib.util.spec_from_file_location("_synced_bk_auth", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _cookie(name: str, value: str) -> dict[str, str]:
    return {"Cookie": f"{name}={value}"}


def _build_app(
    bk_auth_mod: ModuleType,
    *,
    handler: Callable[[httpx2.Request], httpx2.Response] | None = None,
    exempt_paths: tuple[str, ...] = (),
    exempt_prefixes: tuple[str, ...] = (),
) -> FastAPI:
    app = FastAPI()
    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) if handler is not None else None
    bk_auth_mod.install_bk_auth(
        app,
        exempt_paths=exempt_paths,
        exempt_prefixes=exempt_prefixes,
        http_client=http_client,
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/favicon.ico")
    def favicon() -> dict[str, str]:
        return {"icon": "ok"}

    @app.get("/static/app.js")
    def static_asset() -> dict[str, str]:
        return {"asset": "ok"}

    current_user_dep = Depends(bk_auth_mod.get_current_user)
    optional_user_dep = Depends(bk_auth_mod.get_optional_user)

    @app.get("/")
    def home(user: Any = current_user_dep) -> dict[str, Any]:
        return {"page": "home", "username": user.username, "is_preview": user.is_preview}

    @app.get("/dashboard")
    def dashboard(user: Any = current_user_dep) -> dict[str, Any]:
        return {"username": user.username}

    @app.get("/api/items")
    def api_items(user: Any = current_user_dep) -> dict[str, Any]:
        return {"items": [], "username": user.username, "tenant_id": user.tenant_id}

    @app.post("/submit")
    def submit(user: Any = current_user_dep) -> dict[str, str]:
        return {"by": user.username}

    @app.get("/public")
    def public(user: Any = optional_user_dep) -> dict[str, Any]:
        return {"username": user.username if user is not None else None}

    @app.get("/public-strict")
    def public_strict(request: Request) -> dict[str, Any]:
        user = bk_auth_mod.get_current_user(request)
        return {"username": user.username}

    return app


class TestModeResolutionAndFailClosed:
    def test_preview_mode_injects_preview_user(self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_SPARK_PREVIEW_MODE", "1")
        monkeypatch.setenv("APP_SPARK_PREVIEW_USER", "alice-dev")
        monkeypatch.setenv("APP_SPARK_PREVIEW_DISPLAY_NAME", "Alice Dev")

        with TestClient(_build_app(bk_auth_mod)) as client:
            home = client.get("/")
            userinfo = client.get("/api/accounts/userinfo/")

        assert home.status_code == 200
        assert home.json() == {"page": "home", "username": "alice-dev", "is_preview": True}
        assert userinfo.status_code == 200
        assert userinfo.json() == {
            "authenticated": True,
            "username": "alice-dev",
            "display_name": "Alice Dev",
            "tenant_id": None,
            "is_preview": True,
        }

    def test_production_ignores_preview_mode_flag(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APP_SPARK_PREVIEW_MODE", "1")
        monkeypatch.setenv("BKPAAS_APP_ID", "demo-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "top-secret")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://login.example.com/")

        with TestClient(_build_app(bk_auth_mod)) as client:
            resp = client.get("/api/accounts/userinfo/")

        assert resp.status_code == 401
        assert resp.json() == {"authenticated": False, "login_url": "https://login.example.com/"}

    def test_unconfigured_fails_closed_while_healthz_stays_open(self, bk_auth_mod: ModuleType) -> None:
        with TestClient(_build_app(bk_auth_mod)) as client:
            assert client.get("/healthz").status_code == 200
            assert client.get("/static/app.js").status_code == 200
            assert client.get("/favicon.ico").status_code == 200
            assert client.get("/").status_code == 503
            assert client.get("/api/items").status_code == 503
            assert client.get("/api/accounts/userinfo/").status_code == 503

    def test_missing_app_secret_fails_closed_for_bk_token(
        self,
        bk_auth_mod: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "demo-app")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://login.example.com/")

        with (
            caplog.at_level(logging.ERROR),
            TestClient(_build_app(bk_auth_mod)) as client,
        ):
            resp = client.get("/api/items", headers=_cookie("bk_token", "tok-raw-secret-123"))

        assert resp.status_code == 503
        assert "BKPAAS_APP_SECRET" in caplog.text
        assert "tok-raw-secret-123" not in caplog.text

    def test_multi_tenant_ignores_bk_ticket(self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "demo-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "sec")
        monkeypatch.setenv("BKPAAS_MULTI_TENANT_MODE", "true")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://login.example.com/")
        monkeypatch.setenv("BKPAAS_LOGIN_TICKET_VERIFY_URL", "https://auth.example.internal/check_token/")

        with TestClient(_build_app(bk_auth_mod)) as client:
            resp = client.get("/api/items", headers=_cookie("bk_ticket", "ticket-xyz"))

        assert resp.status_code == 401

    def test_invalid_explicit_url_fails_closed(self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "demo-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "sec")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "javascript:alert(1)")

        with TestClient(_build_app(bk_auth_mod)) as client:
            assert client.get("/api/items").status_code == 503

    def test_double_install_is_idempotent(self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_SPARK_PREVIEW_MODE", "1")
        app = FastAPI()
        bk_auth_mod.install_bk_auth(app)
        bk_auth_mod.install_bk_auth(app)

        with TestClient(app) as client:
            resp = client.get("/api/accounts/userinfo/")

        assert resp.status_code == 200
        assert resp.json()["authenticated"] is True


class TestBkTokenSingleTenantIsLogin:
    def test_single_tenant_defaults_to_is_login_even_when_api_url_tmpl_is_set(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "my-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "my-secret")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://paas.example.com/login/")
        monkeypatch.setenv("BK_API_URL_TMPL", "https://bkapi.example.com/api/{api_name}/")

        seen: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return httpx2.Response(200, json={"result": True, "code": 0, "data": {"bk_username": "bob"}})

        with TestClient(_build_app(bk_auth_mod, handler=handler)) as client:
            resp = client.get("/api/accounts/userinfo/", headers=_cookie("bk_token", "tok-1"))

        assert resp.status_code == 200
        assert resp.json() == {
            "authenticated": True,
            "username": "bob",
            "display_name": "bob",
            "tenant_id": None,
            "is_preview": False,
        }
        assert len(seen) == 1
        assert str(seen[0].url) == "https://paas.example.com/login/api/v3/is_login/?bk_token=tok-1"
        assert json.loads(seen[0].headers["X-Bkapi-Authorization"]) == {
            "bk_token": "tok-1",
            "bk_app_code": "my-app",
            "bk_app_secret": "my-secret",
        }

    def test_is_login_permission_denied_returns_403_not_302(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "my-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "my-secret")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://paas.example.com/login/")

        def handler(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json={"result": False, "code": 1302403, "message": "forbidden"})

        with TestClient(_build_app(bk_auth_mod, handler=handler)) as client:
            page_resp = client.get("/", headers=_cookie("bk_token", "tok-denied"), follow_redirects=False)
            api_resp = client.get("/api/items", headers=_cookie("bk_token", "tok-denied"))

        assert page_resp.status_code == 403
        assert api_resp.status_code == 403

    def test_is_login_invalid_token_triggers_unauthenticated_flow(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "my-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "my-secret")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://paas.example.com/login/")

        def handler(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json={"result": False, "code": 1302100, "message": "invalid"})

        with TestClient(_build_app(bk_auth_mod, handler=handler)) as client:
            api_resp = client.get("/api/items", headers=_cookie("bk_token", "bad-tok"))
            page_resp = client.get("/", headers=_cookie("bk_token", "bad-tok"), follow_redirects=False)

        assert api_resp.status_code == 401
        assert api_resp.json() == {"authenticated": False, "login_url": "https://paas.example.com/login/"}
        assert page_resp.status_code == 302


class TestBkTokenApigw:
    def test_multi_tenant_uses_apigw_and_strips_trailing_slash(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "mt-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "mt-secret")
        monkeypatch.setenv("BKPAAS_MULTI_TENANT_MODE", "true")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://login.example.com/")
        monkeypatch.setenv("BK_API_URL_TMPL", "https://bkapi.example.com/api/{api_name}/")

        seen: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return httpx2.Response(
                200,
                json={
                    "data": {
                        "bk_username": "u-123",
                        "display_name": "Zhang San",
                        "tenant_id": "tenant-a",
                        "time_zone": "Asia/Shanghai",
                    }
                },
            )

        with TestClient(_build_app(bk_auth_mod, handler=handler)) as client:
            resp = client.get("/api/accounts/userinfo/", headers={"X-User-Bk-Token": "mt-tok"})

        assert resp.status_code == 200
        assert resp.json() == {
            "authenticated": True,
            "username": "u-123",
            "display_name": "Zhang San",
            "tenant_id": "tenant-a",
            "is_preview": False,
        }
        assert len(seen) == 1
        assert (
            str(seen[0].url) == "https://bkapi.example.com/api/bk-login/prod/login/api/v3/open/bk-tokens/userinfo/"
            "?bk_token=mt-tok"
        )
        assert seen[0].headers["X-Bk-Tenant-Id"] == "default"
        assert json.loads(seen[0].headers["X-Bkapi-Authorization"]) == {
            "bk_app_code": "mt-app",
            "bk_app_secret": "mt-secret",
        }

    def test_single_tenant_uses_apigw_when_explicit_url_configured(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "st-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "st-secret")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://login.example.com/")
        monkeypatch.setenv(
            "BKPAAS_LOGIN_USERINFO_APIGW_URL",
            "https://custom-gw.example.com/prod/login/api/v3/open/bk-tokens/userinfo/",
        )

        seen: list[str] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(str(request.url))
            return httpx2.Response(200, json={"data": {"bk_username": "carol"}})

        with TestClient(_build_app(bk_auth_mod, handler=handler)) as client:
            resp = client.get("/api/items", headers=_cookie("bk_token", "tok-gw"))

        assert resp.status_code == 200
        assert resp.json()["username"] == "carol"
        assert seen == ["https://custom-gw.example.com/prod/login/api/v3/open/bk-tokens/userinfo/?bk_token=tok-gw"]

    def test_apigw_403_returns_403_and_500_or_timeout_returns_503(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "mt-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "mt-secret")
        monkeypatch.setenv("BKPAAS_MULTI_TENANT_MODE", "true")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://login.example.com/")
        monkeypatch.setenv("BK_API_URL_TMPL", "https://bkapi.example.com/api/{api_name}/")

        def handler(request: httpx2.Request) -> httpx2.Response:
            tok = request.url.params["bk_token"]
            if tok == "denied":
                return httpx2.Response(403, json={"error": {"message": "no permission"}})
            if tok == "expired":
                return httpx2.Response(401, json={"error": {"message": "expired"}})
            if tok == "timeout":
                raise httpx2.ReadTimeout("timed out")
            return httpx2.Response(500, text="boom")

        with TestClient(_build_app(bk_auth_mod, handler=handler)) as client:
            assert client.get("/", headers=_cookie("bk_token", "denied"), follow_redirects=False).status_code == 403
            assert client.get("/api/items", headers=_cookie("bk_token", "expired")).status_code == 401
            assert client.get("/api/items", headers=_cookie("bk_token", "timeout")).status_code == 503
            assert client.get("/api/items", headers=_cookie("bk_token", "err500")).status_code == 503


class TestBkTicket:
    def test_bk_ticket_verifies_without_app_secret(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "internal-app")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://passport.example.internal/login/")
        monkeypatch.setenv("BKPAAS_LOGIN_TICKET_VERIFY_URL", "https://auth.example.internal/check_token/")

        seen: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return httpx2.Response(200, json={"ret": 0, "msg": "", "data": {"username": "rtx_alice"}})

        with TestClient(_build_app(bk_auth_mod, handler=handler)) as client:
            resp = client.get("/api/accounts/userinfo/", headers=_cookie("bk_ticket", "ticket-xyz"))

        assert resp.status_code == 200
        assert resp.json()["username"] == "rtx_alice"
        assert len(seen) == 1
        assert str(seen[0].url) == "https://auth.example.internal/check_token/?bk_ticket=ticket-xyz"
        assert "X-Bkapi-Authorization" not in seen[0].headers

    def test_bk_ticket_accepts_header_and_rejects_invalid_ticket(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "internal-app")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://passport.example.internal/login/")
        monkeypatch.setenv("BKPAAS_LOGIN_TICKET_VERIFY_URL", "https://auth.example.internal/user/get_info/")

        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url.params.get("bk_ticket") == "good-ticket":
                return httpx2.Response(200, json={"ret": 0, "msg": "", "data": {"username": "rtx_bob"}})
            return httpx2.Response(200, json={"ret": 1001, "msg": "invalid ticket"})

        with TestClient(_build_app(bk_auth_mod, handler=handler)) as client:
            ok_resp = client.get("/api/items", headers={"X-User-Bk-Ticket": "good-ticket"})
            bad_resp = client.get("/api/items", headers=_cookie("bk_ticket", "bad-ticket"))

        assert ok_resp.status_code == 200
        assert ok_resp.json()["username"] == "rtx_bob"
        assert bad_resp.status_code == 401

    def test_bk_ticket_without_verify_url_fails_closed_503(
        self,
        bk_auth_mod: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "internal-app")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://passport.example.internal/login/")

        with (
            caplog.at_level(logging.ERROR),
            TestClient(_build_app(bk_auth_mod)) as client,
        ):
            resp = client.get("/", headers=_cookie("bk_ticket", "ticket-secret-value"), follow_redirects=False)

        assert resp.status_code == 503
        assert "BKPAAS_LOGIN_TICKET_VERIFY_URL" in caplog.text
        assert "ticket-secret-value" not in caplog.text


class TestCacheAndResponseRouting:
    def test_cache_reuses_valid_result_expires_and_evicts_lru(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "cache-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "cache-secret")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://paas.example.com/login/")
        monkeypatch.setattr(bk_auth_mod, "_CACHE_TTL_SECONDS", 10.0)
        monkeypatch.setattr(bk_auth_mod, "_CACHE_MAX_SIZE", 2)

        now = [100.0]
        monkeypatch.setattr(bk_auth_mod.time, "monotonic", lambda: now[0])

        calls: list[str] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            tok = request.url.params["bk_token"]
            calls.append(tok)
            if tok == "bad":
                return httpx2.Response(200, json={"result": False, "code": 1})
            return httpx2.Response(200, json={"result": True, "code": 0, "data": {"bk_username": f"u-{tok}"}})

        app = _build_app(bk_auth_mod, handler=handler)
        with TestClient(app) as client:
            # First hit verifies; second hit uses cache.
            assert client.get("/api/items", headers=_cookie("bk_token", "t1")).status_code == 200
            assert client.get("/api/items", headers=_cookie("bk_token", "t1")).status_code == 200
            assert calls == ["t1"]

            # Failed verification is never cached.
            assert client.get("/api/items", headers=_cookie("bk_token", "bad")).status_code == 401
            assert client.get("/api/items", headers=_cookie("bk_token", "bad")).status_code == 401
            assert calls == ["t1", "bad", "bad"]

            # Fill capacity (max_size=2): t2 + t3 evicts t1 (LRU).
            assert client.get("/api/items", headers=_cookie("bk_token", "t2")).status_code == 200
            assert client.get("/api/items", headers=_cookie("bk_token", "t3")).status_code == 200
            assert client.get("/api/items", headers=_cookie("bk_token", "t1")).status_code == 200
            assert calls == ["t1", "bad", "bad", "t2", "t3", "t1"]

            # Advance past TTL -> forces re-verification.
            now[0] += 11.0
            assert client.get("/api/items", headers=_cookie("bk_token", "t1")).status_code == 200
            assert calls[-1] == "t1"
            assert len(calls) == 7

        # Verify cache never stores raw token as key.
        cache_dict = bk_auth_mod._BoundedTTLCache(max_size=4, ttl_seconds=60)
        key = cache_dict.make_key("bk_token", "raw-secret-tok")
        assert "raw-secret-tok" not in key
        assert len(key) == 64

    def test_302_vs_401_forwarded_headers_sanitization_and_iframe(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "web-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "web-secret")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://login.example.com/plain/?app_id=web-app")

        with TestClient(_build_app(bk_auth_mod)) as client:
            # Page GET uses sanitized X-Forwarded-Proto & X-Forwarded-Host.
            page = client.get(
                "/dashboard?tab=1",
                headers={
                    "X-Forwarded-Proto": "https, http",
                    "X-Forwarded-Host": "app.example.com, internal.local",
                },
                follow_redirects=False,
            )
            assert page.status_code == 302
            parsed = urlsplit(page.headers["location"])
            query = parse_qs(parsed.query)
            assert parsed.scheme == "https"
            assert parsed.netloc == "login.example.com"
            assert query["app_id"] == ["web-app"]
            assert query["c_url"] == ["https://app.example.com/dashboard?tab=1"]

            # Malicious forwarded headers are discarded safely.
            malicious = client.get(
                "/dashboard",
                headers={
                    "X-Forwarded-Proto": "javascript",
                    "X-Forwarded-Host": "evil.com/path?x=1",
                },
                follow_redirects=False,
            )
            assert malicious.status_code == 302
            mal_c_url = parse_qs(urlsplit(malicious.headers["location"]).query)["c_url"][0]
            assert mal_c_url == "http://testserver/dashboard"

            # Iframe navigation returns top-window redirect HTML instead of bare 302.
            iframe = client.get(
                "/dashboard",
                headers={
                    "Sec-Fetch-Dest": "iframe",
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "a.example.com",
                },
                follow_redirects=False,
            )
            assert iframe.status_code == 401
            assert "window.top.location.href" in iframe.text
            assert "https://login.example.com/plain/" in iframe.text

            # API / JSON / XHR / POST requests always receive 401 JSON.
            for resp in (
                client.get("/api/accounts/userinfo/"),
                client.get("/api/items"),
                client.post("/submit"),
                client.get("/dashboard", headers={"Accept": "application/json"}),
                client.get("/dashboard", headers={"X-Requested-With": "XMLHttpRequest"}),
            ):
                assert resp.status_code == 401
                assert resp.json() == {
                    "authenticated": False,
                    "login_url": "https://login.example.com/plain/?app_id=web-app",
                }

    def test_custom_exempt_path_with_optional_user(
        self, bk_auth_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BKPAAS_APP_ID", "web-app")
        monkeypatch.setenv("BKPAAS_APP_SECRET", "web-secret")
        monkeypatch.setenv("BKPAAS_LOGIN_URL", "https://login.example.com/")

        def handler(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json={"result": True, "code": 0, "data": {"bk_username": "dave"}})

        app = _build_app(bk_auth_mod, handler=handler, exempt_paths=("/public", "/public-strict"))
        with TestClient(app) as client:
            assert client.get("/public").json() == {"username": None}
            assert client.get("/public", headers=_cookie("bk_token", "tok-dave")).json() == {"username": "dave"}
            assert client.get("/public-strict").status_code == 401
            assert client.get("/public-strict", headers=_cookie("bk_token", "tok-dave")).json() == {"username": "dave"}
