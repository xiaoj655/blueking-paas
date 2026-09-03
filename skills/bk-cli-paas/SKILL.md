---
name: bk-cli-paas
description: >-
  Operates the Tencent-internal, code-repository BlueKing SaaS
  lifecycle through `bk-cli paas`. Use for CLI setup, authentication,
  or 403 failures;
  app or module creation; authoring app_desc.yaml deployment descriptors;
  variable or add-on service configuration;
  deployment or release diagnosis; and app, module, branch,
  release-history, runtime, process, or log inspection.
---

# bk-cli paas：SaaS 生命周期

平台操作统一使用 `bk-cli paas`。命令集缺少能力时说明控制台路径，不改用 curl、自行拼 REST 或 `bk-cli api`。

## 运行协议

1. **路由**：从用户目标选出最小阶段集合并锁定目标应用、模块与环境。单阶段任务只进入该阶段；多阶段任务按“创建 → 配置 → 部署”推进，且这个顺序不可调换——增强服务未绑定就部署，会让依赖数据库的发布钩子必然失败；改过代码就必须先 push 再部署，平台构建的是远端 commit，不是本地工作区。完成标志是阶段集合与用户请求一一对应，目标唯一。
2. **预检**：每个 `bk-cli paas` 写命令前运行 `scripts/guard.sh`；`scripts/deploy.sh` 内置该预检。Guard 只用于写命令。完成标志是对应写命令前出现 `guard ok`；失败时进入“准备”阶段。
3. **执行**：只读取当前阶段的手册，逐项满足该手册的完成条件后，才加载下一阶段。
4. **交付**：`ok=true` 从 `data` 取结果；`ok=false` 保留完整原始错误。密钥只报告 key。部署类任务额外要求进程已就绪，只有 `status=successful` 不算完成。完成标志是每个请求阶段都有结果或原始失败证据。

命令 flag 最终以 `bk-cli paas <cmd> -h` 为准。`--body` 使用单引号包 JSON。操作既有应用但缺少 `app_code` 时先运行 `get_minimal_app_list`：唯一候选可直接锁定，多个候选必须由用户选择。未指定模块时用 `default`，部署和运行环境未指定时用 `stag`。
文中的 `scripts/` 路径以本技能目录（`SKILL.md` 所在目录）为根。

## 阶段手册

进入阶段前必须读取对应手册，每次只加载当前手册：

| 用户目标或前置失败 | 必读手册 |
|---|---|
| 安装、context、登录、token、Guard、403 | [准备、认证与 403](playbooks/prepare.md) |
| 创建应用、平台仓库或模块 | [创建应用与模块](playbooks/create.md) |
| 设置环境变量、绑定或解绑增强服务 | [配置变量与服务](playbooks/configure.md) |
| 部署、再部署、等待发布、诊断发布失败 | [部署与诊断](playbooks/deploy.md) |
| 查询应用、模块、分支、地址、进程、日志或历史 | [运行状态查询](playbooks/operate.md) |

生成或修改 `app_desc.yaml`、`Dockerfile` 等部署描述文件时，附加读取 [app_desc.yaml 规范](references/app-desc.md)。它给出经 apiserver 源码核验的字段白名单与可套用模板；不在白名单内的字段会被平台静默丢弃，是“部署成功但不生效”的主要来源。
