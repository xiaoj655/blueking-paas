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
"""运行环境敏感变量加密：开关判定、敏感变量识别、每应用每环境密钥管理、密文替换 + 密钥注入。

设计详见 `docs/reqs/增强服务密码类数据加密/技术方案.md`。核心链路(仅云原生 BkApp 应用):
部署时在 BkApp manifest 成型后、下发前做一次统一后处理 —— 识别敏感变量 → 取/惰性生成该应用该环境
的 runtime key → 用 blue-krill cipher 重新加密其值 → 追加统一密钥变量 `BKPAAS_ENCRYPT_SECRET_KEY`。

加密是增强项：任何环节故障都不得阻断部署,一律降级明文 + warning 日志。因密文自带 `bkcrypt$`/`sm4ctr$`
前缀,降级明文(无前缀) 不会被下游 SDK 误当密文。
"""

import logging
import secrets
from typing import Set

from blue_krill.encrypt.handler import EncryptHandler
from cryptography.fernet import Fernet
from django.conf import settings

from paas_wl.bk_app.cnative.specs.crd import bk_app as crd
from paasng.accessories.servicehub.manager import mixed_service_mgr
from paasng.accessories.servicehub.sharing import ServiceSharingManager
from paasng.platform.applications.constants import AppFeatureFlag
from paasng.platform.applications.models import AppRuntimeEncryptionKey, ModuleEnvironment
from paasng.platform.engine.models.config_var import ENVIRONMENT_ID_FOR_GLOBAL, ConfigVar

logger = logging.getLogger(__name__)

# 随容器下发的统一密钥变量名,增强服务凭证与用户敏感变量共用同一把 runtime key
SECRET_KEY_ENV_NAME = "BKPAAS_ENCRYPT_SECRET_KEY"


def is_global_encrypt_enabled() -> bool:
    """平台全局「运行环境敏感变量加密」总开关。读取失败/缺省一律视为关闭(可用性优先)。"""
    try:
        return bool(settings.ENABLE_ENCRYPT_SENSITIVE_ENV_VARS)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to read ENABLE_ENCRYPT_SENSITIVE_ENV_VARS setting, treat as disabled")
        return False


def is_encrypt_enabled(env: ModuleEnvironment) -> bool:
    """判定某环境是否启用运行环境敏感变量加密：全局开关与应用级 feature flag 需同时开启。

    任一读取异常一律视为关闭,保证加密故障不阻断部署。
    """
    if not is_global_encrypt_enabled():
        return False
    try:
        return env.application.feature_flag.has_feature(AppFeatureFlag.ENCRYPT_SENSITIVE_ENV_VARS)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to read app-level encrypt feature flag for env %s, treat as disabled", env)
        return False


def collect_sensitive_keys(env: ModuleEnvironment) -> Set[str]:
    """汇总该环境需要加密的敏感环境变量 key 集合(以最终注入的 env key 为准)。

    两个来源:
    - 增强服务:各 group 的 `should_hidden_fields` ∪ `should_remove_fields`(均为已加前缀大写的 env key),
      未声明的服务字段保持明文(向后兼容)。
    - 用户变量:`ConfigVar.is_sensitive == True` 的变量 key。
    """
    keys: Set[str] = set()

    # 增强服务(含绑定与共享)声明的敏感字段
    try:
        var_groups = ServiceSharingManager(env.module).get_env_variable_groups(
            env, filter_enabled=True
        ) + mixed_service_mgr.get_env_var_groups(env.get_engine_app(), filter_enabled=True)
        for group in var_groups:
            keys.update(group.should_hidden_fields or [])
            keys.update(group.should_remove_fields or [])
    except Exception:  # noqa: BLE001
        logger.warning("Failed to collect sensitive addon fields for env %s, skip addon keys", env, exc_info=True)

    # 用户标记为敏感的环境变量(global + 当前环境)
    try:
        sensitive_config_vars = ConfigVar.objects.filter(
            module=env.module, environment_id__in=(ENVIRONMENT_ID_FOR_GLOBAL, env.id), is_sensitive=True
        )
        keys.update(var.key for var in sensitive_config_vars)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to collect sensitive config vars for env %s, skip user keys", env, exc_info=True)

    return keys


def _generate_runtime_key() -> str:
    """按当前平台加密算法生成一把 runtime 密钥(裸串)。

    - CLASSIC(Fernet):`Fernet.generate_key()` 生成的 base64 串。
    - SHANGMI(SM4CTR):随机 32 位十六进制串(SM4 取前 16 字节为密钥)。
    """
    if settings.ENCRYPT_CIPHER_TYPE == "SM4CTR":
        return secrets.token_hex(16)
    return Fernet.generate_key().decode()


def get_or_create_runtime_key(env: ModuleEnvironment) -> str:
    """惰性获取该应用该环境的 runtime 密钥,不存在则生成并持久化。

    幂等由 `unique_together=(application, environment)` + `get_or_create` 保证。
    """
    application = env.application
    obj, _ = AppRuntimeEncryptionKey.objects.get_or_create(
        application=application,
        environment=env.environment,
        defaults={
            "key": _generate_runtime_key(),
            "tenant_id": application.tenant_id,
        },
    )
    return obj.key


def _try_encrypt(handler: EncryptHandler, value: str) -> str:
    """加密单个变量值,失败则降级返回明文 + warning(不影响其余变量与部署流程)。"""
    try:
        return handler.encrypt(value)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to encrypt a sensitive env var, degrade to plaintext", exc_info=True)
        return value


def apply_runtime_encryption(model_res: crd.BkAppResource, env: ModuleEnvironment):
    """统一后处理：对成型 BkApp manifest 中的敏感变量做密文替换,并注入统一密钥变量。

    仅处理 global configuration.env 与目标 env 的 overlay 条目(其它环境 overlay 会在部署那个
    环境时用对应 key 处理)。置于 `get_bkapp_resource_for_deploy` 出口,不侵入各 constructor。

    :param model_res: 成型的 BkApp 资源对象,将被原地修改。
    :param env: 目标部署环境。
    """
    # D5:双开关未同时开启 → 直接返回,走原明文注入逻辑
    if not is_encrypt_enabled(env):
        return

    sensitive_keys = collect_sensitive_keys(env)
    if not sensitive_keys:
        return

    # runtime key 生成/读取失败 → 该应用加密不可用,降级明文 + warning,部署继续
    try:
        key = get_or_create_runtime_key(env)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to get or create runtime key for env %s, degrade to plaintext", env, exc_info=True)
        return

    handler = EncryptHandler(secret_key=key)  # type: ignore[arg-type]  # blue-krill 支持 str 密钥

    # 加密 global configuration.env 中的敏感条目
    for var in model_res.spec.configuration.env:
        if var.name in sensitive_keys:
            var.value = _try_encrypt(handler, var.value)

    # 加密目标 env 的 overlay 敏感条目
    overlay = model_res.spec.envOverlay
    if overlay and overlay.envVariables:
        for ov in overlay.envVariables:
            if ov.envName == env.environment and ov.name in sensitive_keys:
                ov.value = _try_encrypt(handler, ov.value)

    # 注入统一密钥变量(global + 目标 env overlay 各一),确保运行时可见
    _inject_secret_key_var(model_res, env, key)


def _inject_secret_key_var(model_res: crd.BkAppResource, env: ModuleEnvironment, key: str):
    """将统一密钥变量 `BKPAAS_ENCRYPT_SECRET_KEY` 注入到 global env 与目标 env overlay。"""
    # 覆盖式写入 global configuration.env
    model_res.spec.configuration.env = [
        var for var in model_res.spec.configuration.env if var.name != SECRET_KEY_ENV_NAME
    ]
    model_res.spec.configuration.env.append(crd.EnvVar(name=SECRET_KEY_ENV_NAME, value=key))

    # 覆盖式写入目标 env overlay
    overlay = model_res.spec.envOverlay
    if not overlay:
        overlay = model_res.spec.envOverlay = crd.EnvOverlay(envVariables=[])
    env_variables = overlay.envVariables or []
    env_variables = [
        ov for ov in env_variables if not (ov.envName == env.environment and ov.name == SECRET_KEY_ENV_NAME)
    ]
    env_variables.append(crd.EnvVarOverlay(envName=env.environment, name=SECRET_KEY_ENV_NAME, value=key))
    overlay.envVariables = env_variables
