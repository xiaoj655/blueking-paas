# 部署与诊断

执行前读取 [commands.md](../commands.md) 的“部署”和“发布与进程”。部署脚本会运行 Guard，并等待终态。

## 1. 锁定版本

运行 `get_repo_branches`，从同一个 `results[]` 项取得真实分支名与 `revision`。分支可能是 `main`、`master` 或其他名称，以返回值为准。

本节以分支名和 revision 均非空且来自同一项完成。

## 2. 部署到终态

运行：

```bash
scripts/deploy.sh --app-code <code> --module <module> --env <env> \
  --branch <真实分支名> --revision <同一项的 revision>
```

手工执行时，依次调用 `deploy_with_module` 与 `get_deployment_result`，每 5 秒轮询一次，直到 `successful`、`failed` 或 `interrupted`。`pending` 是进行中状态；仅以 `status` 判定终态。

特殊响应：

- `CANNOT_DEPLOY_ONGOING_EXISTS`：错误响应没有 `deployment_id`。运行目标环境的 `get_deployments_list`，从最新记录中选择 `status=pending` 的 `deployment_id` 并跟踪；没有 pending 记录时交付冲突响应与部署历史。
- `FILL_EXTRA_INFO`：记录提示并继续部署。
- `status=successful` 且 `error_detail` 为 `Rolling upgrade`：按成功处理。

本节以目标部署进入三个终态之一完成。脚本超时只结束本地等待；后续操作是继续轮询同一个 `deployment_id`。

## 3. 成功交付

状态为 `successful` 时，用 `module_env_released_state` 或 `list_processes` 读取 `exposed_link.url`。
脚本退出码为 0 只证明部署终态成功；返回中没有 URL 时继续查询 `list_processes`。

## 4. 失败诊断

进入本节时，再读取 [commands.md](../commands.md) 的“日志”“环境变量”和“增强服务”。

状态为 `failed` 或 `interrupted` 时，按顺序收敛：

1. `get_deployment_result` 的 `logs`
2. `streams_history_events --channel_id <deployment_id>`
3. `list_processes`
4. `search_standard_log_with_post`
5. `list_config_vars`
6. `list_module_services`

缺少 `Procfile`/`app_desc.yaml`、变量或服务，或版本选错时，先报告原因。用户同时要求修复时，修复后从“锁定版本”重新部署。

## 完成条件

- 成功：交付 `deployment_id`、`successful` 与访问 URL。
- 失败或中断：交付 `deployment_id`、终态，以及按诊断顺序定位到的原因。
- 冲突且部署历史中没有 pending 记录：交付冲突响应与历史查询结果，标记为阻塞。
- 仍在 `pending`：继续等待；它不满足完成条件。
