# bk-cli paas 命令

这是 [SKILL.md](SKILL.md) 的命令与响应字段参考。进入生命周期阶段后，只读对应章节；执行顺序、默认值和完成条件以阶段手册为准。

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

buildpack 使用 `source_init_template=dj2_with_auth`，并去掉 `dockerfile_path` / `docker_build_args`。

平台新建空白仓库：

```bash
bk-cli paas create_cloud_native_app --body '{"is_plugin_app":false,"code":"bk-demo","name":"bk-demo","source_config":{"source_init_template":"","source_control_type":"tc_git","auto_create_repo":true,"write_template_to_repo":false,"repo_name":"bk-demo","source_origin":1,"source_dir":""},"bkapp_spec":{"build_config":{"build_method":"dockerfile","dockerfile_path":null,"docker_build_args":{}}}}'
```

```bash
bk-cli paas create_module --app_code bk-demo --body '{"name":"api","source_config":{"source_init_template":"","source_origin":1,"source_control_type":"tc_git","source_repo_url":"http://<host>/<org>/<repo>.git","source_dir":""},"bkapp_spec":{"build_config":{"build_method":"dockerfile","dockerfile_path":null,"docker_build_args":{}}}}'
```

## 工蜂 Git

`get_app_info` 返回的 `repo_url` 和 create 使用的 `source_repo_url` 是给 API 的 http(s) 地址。本地 clone / push 优先 SSH：

`http://git.woa.com/<user>/<repo>.git` → `git@git.woa.com:<user>/<repo>.git`

本地 clone/push 固定使用 SSH；环境可能没有 HTTP credential helper，且全局 `url.*.insteadOf` 可能把 HTTP 改写成 HTTPS 后仍返回 401。

push 被 `committer-check` 拒时读取 push 的 remote 输出。邮箱必须属于工蜂账号，常用格式为 `<rtx>@tencent.com`；rtx 不确定就问用户。先确认 HEAD 由当前任务创建且尚未推送，并取得用户对 amend 的明确授权；否则交付 remote 输出。满足条件后保留现有 git config，仅重写当前这一笔提交：

```bash
GIT_COMMITTER_NAME="<当前 name>" GIT_COMMITTER_EMAIL="<rtx>@tencent.com" git commit --amend --author="<当前 name> <rtx>@tencent.com" --no-edit
```

身份重写只使用 `--author`；name 沿用当前 committer，只改邮箱。

## 环境变量

```bash
bk-cli paas list_config_vars --app_code bk-demo --module default
bk-cli paas get_config_var --app_code bk-demo --module default --config_var_key FOO
bk-cli paas set_config_var_value --app_code bk-demo --module default --config_var_key FOO --body '{"environment_name":"_global_","value":"bar","description":"demo","is_sensitive":false}'
bk-cli paas set_config_var_value --app_code bk-demo --module default --config_var_key SECRET --body '{"environment_name":"prod","value":"secret","is_sensitive":true}'
```

## 增强服务

```bash
bk-cli paas list_module_services --app_code bk-demo --module default
python3 scripts/bind_service.py --app-code bk-demo --module default --service redis
python3 scripts/bind_service.py --app-code bk-demo --module default --service GCS-MySQL
python3 scripts/bind_service.py --app-code bk-demo --module default --service redis --plan-id <plan-uuid>
python3 scripts/bind_service.py --app-code bk-demo --module default --service redis --stag-plan-id <stag-plan-uuid> --prod-plan-id <prod-plan-uuid>
bk-cli paas unbind_service --app_code bk-demo --module default --service_id <bound-uuid>
bk-cli paas get_service_instance_by_module --app_code bk-demo --module default --service_id <uuid>
```

绑定必须走脚本；`--service` 只做 `name` / `display_name` 精确匹配，也可改用 `--service-id <unbound-uuid>`。加 `--dry-run` 只解析目标并打印请求体，不执行绑定。

## 部署

部署前的本地校验，不调用平台，可反复运行：

```bash
python3 scripts/preflight.py --repo-dir /path/to/repo --module default
python3 scripts/preflight.py --repo-dir /path/to/repo --build-method buildpack --json
```

`--build-method` 省略时按仓库里有无 `Dockerfile` 推断；`--dockerfile-path` 用于模块 `dockerfile_path` 不是仓库根 `Dockerfile` 的情况。退出码 0 表示无阻塞项。

```bash
bk-cli paas get_repo_branches --app_code bk-demo --module default
bk-cli paas deploy_with_module --app_code bk-demo --module default --env stag --body '{"revision":"<sha>","version_type":"branch","version_name":"<branches 的 name>","advanced_options":{"image_pull_policy":"IfNotPresent"}}'
bk-cli paas get_deployment_result --app_code bk-demo --module default --deployment_id <id>
bk-cli paas get_deployments_list --app_code bk-demo --module default --environment stag --limit 12 --offset 0
bk-cli paas streams_history_events --channel_id <deployment_id>
```

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
