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
import pytest
from blue_krill.encrypt.handler import EncryptHandler

from paas_wl.bk_app.cnative.specs.crd import bk_app as crd
from paasng.platform.applications.constants import AppFeatureFlag
from paasng.platform.applications.models import ApplicationFeatureFlag, AppRuntimeEncryptionKey
from paasng.platform.bkapp_model.encryption import (
    SECRET_KEY_ENV_NAME,
    apply_runtime_encryption,
    collect_sensitive_keys,
    get_or_create_runtime_key,
    is_encrypt_enabled,
)
from paasng.platform.engine.models.config_var import ENVIRONMENT_ID_FOR_GLOBAL, ConfigVar

pytestmark = pytest.mark.django_db(databases=["default", "workloads"])


@pytest.fixture()
def enable_global_switch(settings):
    settings.ENABLE_ENCRYPT_SENSITIVE_ENV_VARS = True


@pytest.fixture()
def enable_app_switch(bk_app):
    ApplicationFeatureFlag.objects.set_feature(AppFeatureFlag.ENCRYPT_SENSITIVE_ENV_VARS, True, bk_app)


class TestIsEncryptEnabled:
    def test_both_off(self, bk_stag_env):
        assert is_encrypt_enabled(bk_stag_env) is False

    def test_only_global_on(self, bk_stag_env, enable_global_switch):
        assert is_encrypt_enabled(bk_stag_env) is False

    def test_only_app_on(self, bk_stag_env, enable_app_switch):
        assert is_encrypt_enabled(bk_stag_env) is False

    def test_both_on(self, bk_stag_env, enable_global_switch, enable_app_switch):
        assert is_encrypt_enabled(bk_stag_env) is True


class TestGetOrCreateRuntimeKey:
    def test_idempotent(self, bk_stag_env):
        key1 = get_or_create_runtime_key(bk_stag_env)
        key2 = get_or_create_runtime_key(bk_stag_env)
        assert key1 == key2
        assert AppRuntimeEncryptionKey.objects.filter(application=bk_stag_env.application).count() == 1

    def test_per_env_isolation(self, bk_stag_env, bk_prod_env):
        stag_key = get_or_create_runtime_key(bk_stag_env)
        prod_key = get_or_create_runtime_key(bk_prod_env)
        assert stag_key != prod_key
        assert AppRuntimeEncryptionKey.objects.filter(application=bk_stag_env.application).count() == 2


class TestCollectSensitiveKeys:
    def test_user_sensitive_vars(self, bk_module, bk_stag_env):
        ConfigVar.objects.create(
            module=bk_module,
            environment_id=ENVIRONMENT_ID_FOR_GLOBAL,
            is_global=True,
            key="SECRET_TOKEN",
            value="v",
            is_sensitive=True,
            tenant_id=bk_module.tenant_id,
        )
        ConfigVar.objects.create(
            module=bk_module,
            environment=bk_stag_env,
            key="PLAIN_VAR",
            value="v",
            is_sensitive=False,
            tenant_id=bk_module.tenant_id,
        )
        keys = collect_sensitive_keys(bk_stag_env)
        assert "SECRET_TOKEN" in keys
        assert "PLAIN_VAR" not in keys


def _build_resource(env_vars=None, overlay_vars=None) -> crd.BkAppResource:
    spec = crd.BkAppSpec()
    if env_vars:
        spec.configuration.env = [crd.EnvVar(name=k, value=v) for k, v in env_vars]
    if overlay_vars:
        spec.envOverlay = crd.EnvOverlay(
            envVariables=[crd.EnvVarOverlay(envName=e, name=k, value=v) for e, k, v in overlay_vars]
        )
    return crd.BkAppResource(apiVersion="paas.bk.tencent.com/v1alpha2", metadata={"name": "x"}, spec=spec)


class TestApplyRuntimeEncryption:
    def test_encrypt_and_inject(self, bk_module, bk_stag_env, enable_global_switch, enable_app_switch):
        ConfigVar.objects.create(
            module=bk_module,
            environment_id=ENVIRONMENT_ID_FOR_GLOBAL,
            is_global=True,
            key="SECRET_TOKEN",
            value="plain-secret",
            is_sensitive=True,
            tenant_id=bk_module.tenant_id,
        )
        res = _build_resource(
            env_vars=[("SECRET_TOKEN", "plain-secret"), ("PLAIN_VAR", "keepme")],
            overlay_vars=[("stag", "SECRET_TOKEN", "plain-secret"), ("prod", "SECRET_TOKEN", "prod-secret")],
        )
        apply_runtime_encryption(res, bk_stag_env)

        env_map = {v.name: v.value for v in res.spec.configuration.env}
        # 敏感变量被替换为密文（带前缀），非敏感变量保持明文
        assert env_map["SECRET_TOKEN"].startswith(("bkcrypt$", "sm4ctr$"))
        assert env_map["PLAIN_VAR"] == "keepme"
        # 注入了统一密钥变量
        assert SECRET_KEY_ENV_NAME in env_map
        key = env_map[SECRET_KEY_ENV_NAME]

        # 用注入的密钥可解密敏感变量
        handler = EncryptHandler(secret_key=key)  # type: ignore
        assert handler.decrypt(env_map["SECRET_TOKEN"]) == "plain-secret"

        # 目标 env(stag) overlay 被加密，其它环境(prod) overlay 不动
        overlay_map = {(o.envName, o.name): o.value for o in res.spec.envOverlay.envVariables}  # type: ignore
        assert overlay_map[("stag", "SECRET_TOKEN")].startswith(("bkcrypt$", "sm4ctr$"))
        assert overlay_map[("prod", "SECRET_TOKEN")] == "prod-secret"
        # 目标 env overlay 也注入了密钥变量
        assert (bk_stag_env.environment, SECRET_KEY_ENV_NAME) in overlay_map

    def test_stag_prod_use_different_keys(
        self, bk_module, bk_stag_env, bk_prod_env, enable_global_switch, enable_app_switch
    ):
        ConfigVar.objects.create(
            module=bk_module,
            environment_id=ENVIRONMENT_ID_FOR_GLOBAL,
            is_global=True,
            key="SECRET_TOKEN",
            value="plain",
            is_sensitive=True,
            tenant_id=bk_module.tenant_id,
        )
        stag_res = _build_resource(env_vars=[("SECRET_TOKEN", "plain")])
        prod_res = _build_resource(env_vars=[("SECRET_TOKEN", "plain")])
        apply_runtime_encryption(stag_res, bk_stag_env)
        apply_runtime_encryption(prod_res, bk_prod_env)

        stag_key = {v.name: v.value for v in stag_res.spec.configuration.env}[SECRET_KEY_ENV_NAME]
        prod_key = {v.name: v.value for v in prod_res.spec.configuration.env}[SECRET_KEY_ENV_NAME]
        assert stag_key != prod_key
