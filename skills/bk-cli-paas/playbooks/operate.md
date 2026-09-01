# 运行状态查询

查询阶段的交付物仅为查询结果；Guard 与各类写操作属于对应写入阶段。

按目标读取 [commands.md](../commands.md) 的最小章节：

| 查询目标 | 章节与命令 |
|---|---|
| 应用是否存在、应用详情 | “应用与模块”：`get_app_info` / `get_minimal_app_list` |
| 模块 | “应用与模块”：`list_app_modules` |
| 分支 | “部署”：`get_repo_branches` |
| 环境变量 | “环境变量”：`list_config_vars` / `get_config_var` |
| 增强服务 | “增强服务”：`list_module_services` |
| 发布状态、访问地址 | “发布与进程”：`module_env_released_state` / `module_env_released_info` |
| 进程 | “发布与进程”：`list_processes` |
| 部署历史 | “部署”：`get_deployments_list` |
| 标准输出与运行日志 | “日志”：`search_standard_log_with_post` |

部署失败调查进入“部署与诊断”，由其固定证据顺序收敛。

## 完成条件

返回用户要求的具体字段、目标模块与环境、日志时间范围，以及命令返回的完整错误。修复请求转入对应写入阶段。
