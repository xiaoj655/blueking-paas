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

"""Platform-owned BlueKing login module synchronized into every workspace."""

from functools import cache
from pathlib import Path

from app_spark_agent.app_arch.files import FileSyncResult, sync_text_files

BK_AUTH_FILENAME = "bk_auth.py"
_TEMPLATE_PATH = Path(__file__).with_name("bk_auth_template.py")


@cache
def render_bk_auth() -> str:
    """Return the canonical ``bk_auth.py`` module written to the workspace."""
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def sync_bk_auth(workspace: Path) -> FileSyncResult:
    """Create or repair the workspace's BlueKing login module."""
    return sync_text_files(workspace, {BK_AUTH_FILENAME: render_bk_auth()})
