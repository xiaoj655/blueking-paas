---
name: bk-cli-paas
description: >-
  Creates, configures, and deploys BlueKing PaaS apps exclusively through
  `bk-cli paas`. Use when the user wants to 创建云原生应用, 创建 SaaS, 部署应用,
  配环境变量, 绑定增强服务, 查部署状态, 看进程/日志, 或 mentions bk-cli paas /
  开发者中心 / PaaS 部署. Do not use for 应用市场上架.
---

# bk-cli paas

只用 `bk-cli paas`，不要 curl / 发明 REST / `bk-cli api`。终点是目标环境部署成功 + 访问地址，不上架。
命令见 [commands.md](commands.md)。flag 以 `bk-cli paas <cmd> -h` 为准。本目录可复制到目标 AI 的 skills 路径使用。

## 约定

用户没提就不要问：

| 项 | 值 |
|---|---|
| `source_control_type` | `tc_git` |
| `source_origin` | `1` |
| `build_method` | `dockerfile`；用户提 buildpack 才改 |
| Dockerfile | `Dockerfile` |
| `source_init_template` | 不传（内部 `docker` 模板不可用） |
| `source_repo_url` | HTTPS；`git@host:path` 会 `Invalid url` |
| `source_dir` | `""` |
| 分支 / 模块 / 环境 | `main` / `default` / `stag`（用户说 prod 就部 prod） |
| 环境变量范围 | 未指定 → `_global_` |
| 网关 `--stage` / `app_tenant_mode` | 不传 |

body 不要 `custom_image`、`source_origin=6`、github/bare_git。不要替用户 `context init`。
仓库根必须有 `Procfile` 或 `app_desc.yaml`（dockerfile 也不例外）。已有 `app_desc.yaml` 不要再补 Procfile。

## Guard

写操作前：`scripts/guard.sh`

`auth check` 不验 token，`get_minimal_app_list` 才暴露失效。失败则让用户登录，不要猜 token：

```bash
bk-cli auth login --access_token="<user-provided>"
bk-cli auth login --bk_app_code="<code>" --bk_app_secret="<secret>" --bk_token="<token>"
```

`--dry-run` URL 是 `bkpaas3` 且全员 403 → 见「403」。
输出 JSON：`ok=true` 读 `data`；`ok=false` 交完整错误。不回显密钥。

## Flag

| 命令 | 标识 |
|---|---|
| 大多数 | `--app_code --module --env` |
| `module_env_released_info` / `module_env_released_state` | `--code --module_name --environment` |
| `bind_service` | body：`code` / `module_name` / `service_id` |
| `set_config_var_value` / `get_config_var` | `--app_code --module --config_var_key` |

`--body` 用单引号包 JSON。缺 `app_code` 先 `get_minimal_app_list`。

## 场景

新建先复述 `code` / HTTPS URL / `build_method`，用户确认再 create。部署说了就做。

### 1 新建并部署

1. Guard；list 里已有 `code` → 场景 2
2. 无 Procfile/app_desc → 按 Dockerfile `CMD` 补 `Procfile`（如 `web: python app.py`）并 push
3. `create_cloud_native_app`（不传 `source_init_template`，URL 用 HTTPS）
4. 有变量 / 中间件就配
5. 场景 2 部署 stag

create 报模板不可用 → 去掉 `source_init_template`。`Invalid url` → 改 HTTPS。已存在 → 场景 2。
push 被 `committer-check` 拒 → 公司邮箱重 commit（不擅自改 git config）。

### 2 再部署

`scripts/deploy.sh --app-code <code> --env stag`

手跑：`get_app_info` → `get_repo_branches` 取 `revision` → `deploy_with_module` → 每 5s `get_deployment_result` 直到终态 → 成功 `module_env_released_state`，失败场景 3。
`CANNOT_DEPLOY_ONGOING_EXISTS` → 跟踪已有 `deployment_id`。改变量或绑服务后必须重部署。

### 3 失败

`get_deployment_result` → `streams_history_events --channel_id <deployment_id>` → `list_processes` → `search_standard_log_with_post` → `list_config_vars` → `list_module_services`。
缺 Procfile/app_desc、漏变量、漏服务、错分支 → 修完场景 2。修不了把原文交给用户。

## 环境变量

未指定环境用 `_global_`。密码类 `is_sensitive: true`。不改 key 名。无删除命令。新建 `value` 必填；更新时 `value` 空则不改原值。命令见 [commands.md](commands.md)。

## 增强服务

`list_module_services` → `unbound[].uuid` → `bind_service`。不编 UUID，不传 `plan_id`。已 `bound` 不绑。解绑仅用户要求。只报凭证 key 名。绑完重部署。

## 查询

| 用户要 | 命令 |
|---|---|
| 应用是否存在 | `get_app_info` / `get_minimal_app_list` |
| 模块 | `list_app_modules` |
| 环境变量 | `list_config_vars` |
| 增强服务 | `list_module_services` |
| 访问地址 | `module_env_released_state` |
| 部署历史 | `get_deployments_list` |
| 进程 | `list_processes` |
| 分支 | `get_repo_branches` |

`APP_NOT_RELEASED` 不是凭证坏了。

## 403

全员 403 `1640301`：先 `--dry-run` 看 URL。`bkpaas3` → `BK_TE_DOMAIN=<内部域> make build`，不要申请 bkpaas3 权限。已是 `paasv3` 再给调用方 `app_code` 申请 API 权限。IAM `9900403` → 业务权限。

部署等到终态后再结束，带上 `deployment_id` + 访问 URL 或失败原因。
