---
name: bk-cli-paas-deploy
description: >-
  Deploys BlueKing PaaS modules and diagnoses failed releases through
  `bk-cli paas`. Use when the user wants to 部署应用, 再部署, 发布到预发布/生产,
  查部署结果, or 部署失败排查.
---

# bk-cli paas 部署

前置：读 [../bk-cli-paas/SKILL.md](../bk-cli-paas/SKILL.md) 的约定 / Guard / Flag。
写操作前：`../bk-cli-paas/scripts/guard.sh`。命令见 [../bk-cli-paas/commands.md](../bk-cli-paas/commands.md)。

终点是目标环境部署成功 + 访问地址。改变量或绑服务后必须重部署。

## 再部署

对应控制台：部署管理 → 预发布 → default → 分支以仓库为准。

部署前必须 `get_repo_branches`：用返回的真实分支名（`main` 或 `master` 都可能，不要默认）和 `results[].revision`。空仓库 seed 后尤其不要猜分支名。

`../bk-cli-paas/scripts/deploy.sh --app-code <code> --env stag --branch <真实分支名>`

手跑：`get_app_info` → `get_repo_branches` 取**分支名 + `revision`** → `deploy_with_module`（带 `advanced_options.image_pull_policy=IfNotPresent`）→ 每 5s `get_deployment_result` 直到终态 → 成功 `module_env_released_state` 或 `list_processes` 的 `exposed_url`，失败见下方。
Dockerfile 小应用首次构建常见约 30s：第一次轮询仍是 `pending` 不当失败，继续每 5s 直到终态。
`CANNOT_DEPLOY_ONGOING_EXISTS` → 跟踪已有 `deployment_id`。
部署前 preparations 报「未完善应用基本信息」/`FILL_EXTRA_INFO` 不挡部署，忽略。
`status=successful` 时 `error_detail` 可能是 `Rolling upgrade`，不当失败。

```bash
bk-cli paas get_repo_branches --app_code bk-demo --module default
bk-cli paas deploy_with_module --app_code bk-demo --module default --env stag --body '{"revision":"<sha>","version_type":"branch","version_name":"<branches 的 name>","advanced_options":{"image_pull_policy":"IfNotPresent"}}'
bk-cli paas get_deployment_result --app_code bk-demo --module default --deployment_id <id>
bk-cli paas get_deployments_list --app_code bk-demo --module default --environment stag --limit 12 --offset 0
bk-cli paas streams_history_events --channel_id <deployment_id>
```

`version_name` / `revision` 都从 `get_repo_branches` 的 `results[]` 取。`channel_id` = `deployment_id`。
`get_deployment_result` 状态：`pending` / `successful` / `failed` / `interrupted`。只看 `status`，不要用 `error_detail` 判断成败。

## 失败

`get_deployment_result`（看 `logs`：准备 / 构建镜像 / 部署）→ `streams_history_events --channel_id <deployment_id>` → `list_processes` → `search_standard_log_with_post` → `list_config_vars` → `list_module_services`。
缺 Procfile/app_desc、漏变量、漏服务、错分支 → 修完再走「再部署」。修不了把原文交给用户。

配变量 / 绑服务见 `bk-cli-paas-ops`。查地址 / 进程 / 日志也可直接用那个 skill。

部署等到终态后再结束，带上 `deployment_id` + 访问 URL 或失败原因。
