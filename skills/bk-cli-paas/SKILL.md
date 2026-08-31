---
name: bk-cli-paas
description: >-
  Shared conventions, auth guard, flags, and 403 handling for BlueKing
  PaaS via `bk-cli paas`. Use when the user mentions bk-cli paas /
  开发者中心 / 登录 / token / 403 / BK_TE_DOMAIN, or as the common
  prerequisite for create / deploy / ops. Do not use for 应用市场上架.
---

# bk-cli paas

只用 `bk-cli paas`，不要 curl / 发明 REST / `bk-cli api`。不上架。
命令见 [commands.md](commands.md)。flag 以 `bk-cli paas <cmd> -h` 为准。

本目录是公共底座（约定 / Guard / 脚本）。功能在同级 skill，按任务只读一个：

| 任务 | skill |
|---|---|
| 新建云原生 / SaaS / 平台空白仓库 | `bk-cli-paas-create` |
| 部署 / 再部署 / 部署失败 | `bk-cli-paas-deploy` |
| 环境变量 / 增强服务 / 查状态进程日志 | `bk-cli-paas-ops` |

单独复制功能 skill 时也要带上本目录（`scripts/` + 约定）。整包复制 `skills/bk-cli-paas*`。

## 约定

用户没提就不要问：

| 项 | 值 |
|---|---|
| `source_control_type` | `tc_git`（腾讯工蜂） |
| `source_origin` | `1`（代码仓库，不是镜像仓库） |
| `build_method` | `dockerfile`；用户提 buildpack 才改 |
| `source_init_template` | `""`（空模板；不要传内部 `docker` 模板名） |
| 仓库类型 | 已有仓库；用户说平台新建 / 空白仓库才走 `auto_create_repo` |
| `source_repo_url` | 已有仓库：工蜂 clone URL（`http`/`https` 都行，不要 `git@`） |
| `source_dir` | `""`（构建目录空 = 仓库根） |
| `dockerfile_path` | `null`（空 = 构建目录下名为 `Dockerfile` 的文件） |
| `docker_build_args` | `{}` |
| `is_plugin_app` | `false` |
| 分支 / 模块 / 环境 | `main` / `default` / `stag`（用户说 prod 就部 prod） |
| 环境变量范围 | 未指定 → `_global_` |
| 网关 `--stage` / `app_tenant_mode` | 不传 |

body 不要 `custom_image`、`source_origin=6`、github/bare_git，不要传 `region`。不要替用户 `context init`。
仓库根必须有 `Procfile` 或 `app_desc.yaml`（dockerfile 也不例外）。控制台推荐 `app_desc.yaml` + `Dockerfile`；已有 `app_desc.yaml` 不要再补 Procfile。

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

## 403

全员 403 `1640301`：先 `--dry-run` 看 URL。`bkpaas3` → `BK_TE_DOMAIN=<内部域> make build`，不要申请 bkpaas3 权限。已是 `paasv3` 再给调用方 `app_code` 申请 API 权限。IAM `9900403` → 业务权限。
