# 配置变量与服务

执行前读取 [commands.md](../commands.md) 的“环境变量”或“增强服务”，只处理用户要求的配置项。

## 环境变量

1. 未指定作用域时使用 `_global_`；显式环境只接受 `stag` 或 `prod`。
2. 密码、token、secret 等凭证设置 `is_sensitive=true`，保持用户给定的 key 名。
3. 新建变量时提供 `value`；更新变量时，空 `value` 表示保留原值。
4. 写入后使用 `get_config_var` 或 `list_config_vars` 回读目标 key 与作用域。

当前命令集负责新增和更新；删除请求转交控制台操作。

## 增强服务

绑定必须发生在部署之前。`app_desc.yaml` 里的 `spec.addons` 在仓库部署路径是空操作，不会创建也不会绑定任何服务；应用的 `hooks.preRelease`（如 `migrate`）或进程启动逻辑一旦要访问数据库，未绑定就会让首次部署失败。多阶段任务里，“配置”阶段必须整体完成后才进入“部署”。

### 绑定

绑定只运行 `scripts/bind_service.py`，不要直接拼 `bind_service --body`。脚本会：

1. 从 `list_module_services` 精确匹配 `name`、`display_name` 或显式 UUID，不接受猜测的 UUID。
2. 区分 `bound`、`shared`、`unbound`；已绑定直接完成，共享服务不尝试重复绑定。
3. 仅对实际返回的 GCS-MySQL UUID 自动补齐已知的 `stag` / `prod` 方案；其他服务由平台选择默认方案。
4. 写入前运行 Guard，成功后再次查询并确认同一 UUID 已进入 `bound`。

普通绑定、显式单方案和分环境方案的命令见 [commands.md](../commands.md)。“未精确匹配”或“匹配不唯一”时，从脚本输出的候选项请用户确认，不做模糊选择。

默认方案返回 `CANNOT_BIND_SERVICE` 时，当前 CLI 无法列出方案。保留完整错误，请用户从控制台取得 plan UUID，再用 `--plan-id`，或同时提供 `--stag-plan-id` 与 `--prod-plan-id` 重试。

### 解绑

1. 运行 `list_module_services`，从 `bound` 取得目标服务 UUID。
2. 运行 `unbind_service --app_code <code> --module <module> --service_id <uuid>`。
3. 再次运行 `list_module_services`，确认目标服务已离开 `bound`。

## 完成条件

每个请求配置项都已回读：变量的 key 与作用域匹配；绑定服务出现在 `bound`；解绑服务已离开 `bound`。

变量或服务变更需要重新部署才会生效。用户要求部署时，完成本节后进入[部署与诊断](deploy.md)；否则明确报告“已写入、待重新部署生效”并结束。
