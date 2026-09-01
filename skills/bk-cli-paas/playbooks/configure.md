# 配置变量与服务

执行前读取 [commands.md](../commands.md) 的“环境变量”或“增强服务”，只处理用户要求的配置项。

## 环境变量

1. 未指定作用域时使用 `_global_`；显式环境只接受 `stag` 或 `prod`。
2. 密码、token、secret 等凭证设置 `is_sensitive=true`，保持用户给定的 key 名。
3. 新建变量时提供 `value`；更新变量时，空 `value` 表示保留原值。
4. 写入后使用 `get_config_var` 或 `list_config_vars` 回读目标 key 与作用域。

当前命令集负责新增和更新；删除请求转交控制台操作。

## 增强服务

### 绑定

1. 先运行 `list_module_services`，以 `bound` 和 `unbound` 的实际数据选择服务。
2. 已在 `bound` 中的服务直接满足绑定条件。
3. GCS-MYSQL 使用 `env_plan_id_map`，`stag` 与 `prod` 同时提供同一个 plan UUID。
4. 其他服务使用 `unbound[].uuid`；有可用默认方案时可省略 plan。
5. 绑定后重新运行 `list_module_services`，确认服务进入 `bound`。

默认绑定返回 `CANNOT_BIND_SERVICE` 时停止该次绑定，请用户从控制台取得 plan UUID 后再执行。

### 解绑

1. 运行 `list_module_services`，从 `bound` 取得目标服务 UUID。
2. 运行 `unbind_service --app_code <code> --module <module> --service_id <uuid>`。
3. 再次运行 `list_module_services`，确认目标服务已离开 `bound`。

## 完成条件

每个请求配置项都已回读：变量的 key 与作用域匹配；绑定服务出现在 `bound`；解绑服务已离开 `bound`。

变量或服务变更需要重新部署才会生效。用户要求部署时，完成本节后进入“部署与诊断”；否则明确报告“已写入、待重新部署生效”并结束。
