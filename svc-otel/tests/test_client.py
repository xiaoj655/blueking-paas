# -*- coding: utf-8 -*-
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

from unittest.mock import MagicMock, patch

import pytest
from bkapi_client_core.exceptions import APIGatewayResponseError
from svc_otel.bkmonitorv3.backend.apigw import Group
from svc_otel.bkmonitorv3.backend.esb import MonitorV3Group
from svc_otel.bkmonitorv3.client import BkMonitorClient
from svc_otel.bkmonitorv3.exceptions import (
    BkMonitorApiError,
    BkMonitorApmApplicationDoesNotExist,
    BkMonitorGatewayServiceError,
)


@pytest.fixture()
def backend():
    return MagicMock()


@pytest.fixture()
def client(backend):
    return BkMonitorClient(backend)


class TestGetApm:
    def test_return_token(self, client, backend):
        backend.detail_apm_application.return_value = {
            "result": True,
            "data": {"app_name": "bkapp_demo_stag", "token": "token-from-monitor"},
        }

        token = client.get_apm("bkapp_demo_stag", "bkpaas__demo")

        assert token == "token-from-monitor"
        backend.detail_apm_application.assert_called_once_with(
            data={"app_name": "bkapp_demo_stag", "space_uid": "bkpaas__demo"}
        )

    def test_raise_when_application_does_not_exist(self, client, backend):
        backend.detail_apm_application.return_value = {
            "result": False,
            "message": "the application does not exist",
            "data": {},
        }

        with pytest.raises(BkMonitorApmApplicationDoesNotExist, match="does not exist"):
            client.get_apm("bkapp_demo_stag", "bkpaas__demo")

    @pytest.mark.parametrize("data", [{}, None, "unexpected", {"token": ""}])
    def test_raise_when_token_is_missing(self, client, backend, data):
        backend.detail_apm_application.return_value = {"result": True, "data": data}

        with pytest.raises(BkMonitorApiError, match="token is empty"):
            client.get_apm("bkapp_demo_stag", "bkpaas__demo")

    def test_wrap_gateway_error(self, client, backend):
        backend.detail_apm_application.side_effect = APIGatewayResponseError("gateway error")

        with pytest.raises(BkMonitorGatewayServiceError, match="Failed to get APM"):
            client.get_apm("bkapp_demo_stag", "bkpaas__demo")


class TestCreateApm:
    def test_return_token(self, client, backend):
        backend.apm_create_application.return_value = {"result": True, "data": "created-token"}

        assert client.create_apm("bkapp_demo_stag", "bkpaas__demo") == "created-token"

    @pytest.mark.parametrize("data", [{}, None, ""])
    def test_raise_when_token_is_missing(self, client, backend, data):
        backend.apm_create_application.return_value = {"result": True, "data": data}

        with pytest.raises(BkMonitorApiError, match="token is empty"):
            client.create_apm("bkapp_demo_stag", "bkpaas__demo")


class TestGetOrCreateApm:
    def test_reuse_existing_application(self, client):
        with (
            patch.object(client, "get_apm", return_value="existing-token") as get_apm,
            patch.object(client, "create_apm") as create_apm,
        ):
            assert client.get_or_create_apm("bkapp_demo_stag", "bkpaas__demo") == "existing-token"

        get_apm.assert_called_once_with("bkapp_demo_stag", "bkpaas__demo")
        create_apm.assert_not_called()

    def test_create_when_application_does_not_exist(self, client):
        with (
            patch.object(
                client,
                "get_apm",
                side_effect=BkMonitorApmApplicationDoesNotExist("application does not exist"),
            ),
            patch.object(client, "create_apm", return_value="created-token") as create_apm,
        ):
            assert client.get_or_create_apm("bkapp_demo_stag", "bkpaas__demo") == "created-token"

        create_apm.assert_called_once_with("bkapp_demo_stag", "bkpaas__demo")

    def test_do_not_match_create_error_message(self, client):
        with (
            patch.object(
                client,
                "get_apm",
                side_effect=BkMonitorApmApplicationDoesNotExist("application does not exist"),
            ) as get_apm,
            patch.object(client, "create_apm", side_effect=BkMonitorApiError("应用名称已存在")),
            pytest.raises(BkMonitorApiError, match="应用名称已存在"),
        ):
            client.get_or_create_apm("bkapp_demo_stag", "bkpaas__demo")

        assert get_apm.call_count == 1

    def test_propagate_other_query_errors(self, client):
        with (
            patch.object(client, "get_apm", side_effect=BkMonitorApiError("permission denied")),
            patch.object(client, "create_apm") as create_apm,
            pytest.raises(BkMonitorApiError, match="permission denied"),
        ):
            client.get_or_create_apm("bkapp_demo_stag", "bkpaas__demo")

        create_apm.assert_not_called()


def test_detail_operation_paths():
    assert Group().detail_apm_application.path == "/app/apm/detail_apm_application/"
    assert MonitorV3Group().detail_apm_application.path == "/api/c/compapi/v2/monitor_v3/detail_apm_application/"
