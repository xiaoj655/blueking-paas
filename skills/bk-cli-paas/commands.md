# bk-cli paas 命令

公共手册。按任务读同级 skill：新建 `bk-cli-paas-create`，部署 `bk-cli-paas-deploy`，变量/服务/查询 `bk-cli-paas-ops`。约定和 Guard 见 [SKILL.md](SKILL.md)。

全局：`--context` `--dry-run` `--insecure` `-v`。

## 应用与模块

```bash
bk-cli paas get_minimal_app_list
bk-cli paas get_minimal_app_list --app_status normal --source_origin 1
bk-cli paas get_app_info --app_code bk-demo
bk-cli paas list_app_modules --app_code bk-demo
```

`--app_status`：`not_deployed` / `normal` / `offline`。

```bash
bk-cli paas create_cloud_native_app --body '{"is_plugin_app":false,"code":"bk-demo","name":"bk-demo","source_config":{"source_init_template":"","source_origin":1,"source_control_type":"tc_git","source_repo_url":"http://<host>/<org>/<repo>.git","source_dir":""},"bkapp_spec":{"build_config":{"build_method":"dockerfile","dockerfile_path":null,"docker_build_args":{}}}}'
```

空模板：`source_init_template=""`。已有仓库：`source_repo_url` 用工蜂 clone 地址（http/https 都行，不要 `git@`）。`dockerfile_path` 空则 `null`。buildpack 时才加 `source_init_template=dj2_with_auth`，去掉 `dockerfile_path` / `docker_build_args`。

平台新建空白仓库（不要传 `source_repo_url`）：

```bash
bk-cli paas create_cloud_native_app --body '{"is_plugin_app":false,"code":"bk-demo","name":"bk-demo","source_config":{"source_init_template":"","source_control_type":"tc_git","auto_create_repo":true,"write_template_to_repo":false,"repo_name":"bk-demo","source_origin":1,"source_dir":""},"bkapp_spec":{"build_config":{"build_method":"dockerfile","dockerfile_path":null,"docker_build_args":{}}}}'
```

`repo_name` 默认等于 `code`。指定工蜂项目组才加 `repo_group`。空模板必须 `write_template_to_repo=false`。创建后仓库是空的，`get_app_info` 取 `repo_url` 再 clone / 补文件 / push。

```bash
bk-cli paas create_module --app_code bk-demo --body '{"name":"api","source_config":{"source_init_template":"","source_origin":1,"source_control_type":"tc_git","source_repo_url":"http://<host>/<org>/<repo>.git","source_dir":""},"bkapp_spec":{"build_config":{"build_method":"dockerfile","dockerfile_path":null,"docker_build_args":{}}}}'
```

模块名：小写字母/数字/连字符，最长 16。

## 环境变量

`environment_name`：`stag` / `prod` / `_global_`。

```bash
bk-cli paas list_config_vars --app_code bk-demo --module default
bk-cli paas get_config_var --app_code bk-demo --module default --config_var_key FOO
bk-cli paas set_config_var_value --app_code bk-demo --module default --config_var_key FOO --body '{"environment_name":"_global_","value":"bar","description":"demo","is_sensitive":false}'
bk-cli paas set_config_var_value --app_code bk-demo --module default --config_var_key SECRET --body '{"environment_name":"prod","value":"secret","is_sensitive":true}'
```

`--config_var_key` 是变量名。`--body` 里 `environment_name` 必填。

## 增强服务

```bash
bk-cli paas list_module_services --app_code bk-demo --module default
bk-cli paas bind_service --body '{"code":"bk-demo","service_id":"<unbound-uuid>","module_name":"default"}'
bk-cli paas get_service_instance_by_module --app_code bk-demo --module default --service_id <uuid>
bk-cli paas unbind_service --app_code bk-demo --module default --service_id <uuid>
```

`service_id` 只用 `unbound[].uuid`。`bind_service` 没有 `--app_code`。

## 部署

```bash
bk-cli paas get_repo_branches --app_code bk-demo --module default
bk-cli paas deploy_with_module --app_code bk-demo --module default --env stag --body '{"revision":"<sha>","version_type":"branch","version_name":"main","advanced_options":{"image_pull_policy":"IfNotPresent"}}'
bk-cli paas get_deployment_result --app_code bk-demo --module default --deployment_id <id>
bk-cli paas get_deployments_list --app_code bk-demo --module default --environment stag --limit 12 --offset 0
bk-cli paas streams_history_events --channel_id <deployment_id>
```

`revision` 从 `get_repo_branches` 的 `results[].revision` 取。`channel_id` = `deployment_id`。
`get_deployment_result` 状态：`pending` / `successful` / `failed` / `interrupted`。
`status=successful` 时 `error_detail` 可能是 `Rolling upgrade`，只看 `status`。

## 发布与进程

flag 是 `--code --module_name --environment`：

```bash
bk-cli paas module_env_released_state --code bk-demo --module_name default --environment stag
bk-cli paas module_env_released_info --code bk-demo --module_name default --environment prod
bk-cli paas list_processes --app_code bk-demo --module default --env stag
```

未发布：`APP_NOT_RELEASED`。地址看 `exposed_link.url`。进程看 `instances.items` 的 `state` / `state_message` / `restart_count`。

## 日志

```bash
bk-cli paas search_standard_log_with_post --app_code bk-demo --module default --time_range 1h --body '{"query":{"query_string":"","terms":{"environment":["stag"]}}}'
bk-cli paas search_standard_log_with_post --app_code bk-demo --module default --time_range customized --start_time '2026-08-24 10:00:00' --end_time '2026-08-24 11:00:00' --body '{"query":{"query_string":"error","terms":{"environment":["prod"],"process_id":["web"]}}}'
```

`--time_range`：`5m` `1h` `3h` `6h` `12h` `1d` `3d` `7d` `customized`。`customized` 要 `--start_time` / `--end_time`（`2006-01-02 15:04:05`）。
