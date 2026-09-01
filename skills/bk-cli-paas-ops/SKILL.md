---
name: bk-cli-paas-ops
description: >-
  Configures env vars and add-on services, and queries BlueKing PaaS
  app status via `bk-cli paas`. Use when the user wants to 配环境变量,
  绑定增强服务, 查部署状态, 看进程/日志, 查访问地址, or 列应用/模块/分支.
---

# bk-cli paas 运维

前置：读 [../bk-cli-paas/SKILL.md](../bk-cli-paas/SKILL.md) 的约定 / Guard / Flag。
写操作前：`../bk-cli-paas/scripts/guard.sh`。命令见 [../bk-cli-paas/commands.md](../bk-cli-paas/commands.md)。

改变量或绑服务后必须重部署 → `bk-cli-paas-deploy`。`APP_NOT_RELEASED` 不是凭证坏了。

## 环境变量

未指定环境用 `_global_`。密码类 `is_sensitive: true`。不改 key 名。无删除命令。新建 `value` 必填；更新时 `value` 空则不改原值。`--config_var_key` 是变量名。`--body` 里 `environment_name` 必填。

`environment_name`：`stag` / `prod` / `_global_`。

```bash
bk-cli paas list_config_vars --app_code bk-demo --module default
bk-cli paas get_config_var --app_code bk-demo --module default --config_var_key FOO
bk-cli paas set_config_var_value --app_code bk-demo --module default --config_var_key FOO --body '{"environment_name":"_global_","value":"bar","description":"demo","is_sensitive":false}'
bk-cli paas set_config_var_value --app_code bk-demo --module default --config_var_key SECRET --body '{"environment_name":"prod","value":"secret","is_sensitive":true}'
```

## 增强服务

`list_module_services` → 已 `bound` 不绑。解绑仅用户要求（该网关 `unbind_service` 未注册，报 `1640401`，只能控制台解绑）。只报凭证 key 名。绑完重部署。`bind_service` 没有 `--app_code`。

GCS-MYSQL 直绑（`code` 换成目标应用；不要不带 plan 试，不要只传 stag）：

```bash
bk-cli paas bind_service --body '{"code":"bk-demo","service_id":"946ee404-df67-4013-a92f-9cc116ff50dc","module_name":"default","env_plan_id_map":{"stag":"8c52a7f8-a8ff-47da-b0f0-0ef744b37562","prod":"8c52a7f8-a8ff-47da-b0f0-0ef744b37562"}}'
```

其它服务：`unbound[].uuid` → `bind_service`（可不带 plan）。默认绑定报 `CANNOT_BIND_SERVICE` → 不要重试、不要用 plan 名称。让用户控制台抓 `env_plan_id_map` 的 plan UUID 再绑。

```bash
bk-cli paas list_module_services --app_code bk-demo --module default
bk-cli paas bind_service --body '{"code":"bk-demo","service_id":"<unbound-uuid>","module_name":"default"}'
bk-cli paas get_service_instance_by_module --app_code bk-demo --module default --service_id <uuid>
```

## 查询

| 用户要 | 命令 |
|---|---|
| 应用是否存在 | `get_app_info` / `get_minimal_app_list` |
| 模块 | `list_app_modules` |
| 环境变量 | `list_config_vars` |
| 增强服务 | `list_module_services` |
| 访问地址 | `module_env_released_state` / `list_processes` 的 `exposed_url` |
| 部署历史 | `get_deployments_list` |
| 进程 | `list_processes` |
| 分支 | `get_repo_branches` |
| 日志 | `search_standard_log_with_post` |

```bash
bk-cli paas get_minimal_app_list
bk-cli paas get_app_info --app_code bk-demo
bk-cli paas list_app_modules --app_code bk-demo
bk-cli paas get_repo_branches --app_code bk-demo --module default
bk-cli paas module_env_released_state --code bk-demo --module_name default --environment stag
bk-cli paas module_env_released_info --code bk-demo --module_name default --environment prod
bk-cli paas list_processes --app_code bk-demo --module default --env stag
bk-cli paas get_deployments_list --app_code bk-demo --module default --environment stag --limit 12 --offset 0
```

`module_env_released_*` 的 flag 是 `--code --module_name --environment`。未发布：`APP_NOT_RELEASED`。地址看 `exposed_link.url`。进程看 `instances.items` 的 `state` / `state_message` / `restart_count`。

```bash
bk-cli paas search_standard_log_with_post --app_code bk-demo --module default --time_range 1h --body '{"query":{"query_string":"","terms":{"environment":["stag"]}}}'
bk-cli paas search_standard_log_with_post --app_code bk-demo --module default --time_range customized --start_time '2026-08-24 10:00:00' --end_time '2026-08-24 11:00:00' --body '{"query":{"query_string":"error","terms":{"environment":["prod"],"process_id":["web"]}}}'
```

`--time_range`：`5m` `1h` `3h` `6h` `12h` `1d` `3d` `7d` `customized`。`customized` 要 `--start_time` / `--end_time`（`2006-01-02 15:04:05`）。
