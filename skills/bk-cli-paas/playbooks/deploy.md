# 部署与诊断

执行前读取 [commands.md](../commands.md) 的“部署”和“发布与进程”。部署脚本依次做：本地预检 → Guard → 解析 revision → 比对本地 HEAD → 部署 → 等待终态 → 等待进程就绪。

## 1. 部署前置

部署一次要几分钟，失败大多能在本地提前发现。进入部署前确认三件事：

1. **描述文件可用。** 本地有仓库检出时，把路径传给脚本的 `--repo-dir`，预检自动执行；没有检出时，按 [app_desc.yaml 规范](../references/app-desc.md) 核对 web 进程带 `exposedType`、端口一致、进程名不超 12 字符。
2. **代码已推送。** 平台构建的是远端仓库上的 commit，不是本地工作区。改完没 push 就部署，部署照样成功，但跑的是旧代码——这是“部署完还是原来的”最常见的原因。传了 `--repo-dir` 时脚本会比对本地 HEAD 与平台解析出的 revision，不一致就中止并说明差了几个提交。
3. **依赖的增强服务已绑定。** `spec.addons` 在仓库部署路径不生效，写了也不会创建服务。`hooks.preRelease` 里有 `migrate` 一类命令，或进程启动就要读 DB 时，必须先完成[配置变量与服务](configure.md)的绑定，再部署。顺序反了必定挂在 Pre-Release-Hook。

## 2. 锁定版本

运行 `get_repo_branches`，从同一个 `results[]` 项取得真实分支名与 `revision`。分支可能是 `main`、`master` 或其他名称，以返回值为准。省略 `--revision` 时脚本会自己解析。

本节以分支名和 revision 均非空且来自同一项完成。

## 3. 部署到终态

```bash
scripts/deploy.sh --app-code <code> --module <module> --env <env> \
  --branch <真实分支名> --repo-dir <本地仓库路径>
```

退出码即结论，不要只看 `status`：

| 退出码 | 含义 | 下一步 |
|---|---|---|
| 0 | 部署成功且所有进程就绪 | 交付 `deployment_id` 与 `url` |
| 1 | 预检未通过，或部署终态为 `failed` / `interrupted` | 进入“失败诊断” |
| 3 | 部署成功但进程起不来 | 进入“失败诊断”，重点看 `processes.broken` |

两个阻断项各有对应的放行开关，只在确认过后果时才加：

| 开关 | 放行的检查 |
|---|---|
| `--skip-preflight` | 描述文件预检报了 error |
| `--allow-stale-revision` | 本地 HEAD 与平台 revision 不一致（回滚到旧 commit 时用得上） |

构建耗时随依赖数量变化，`pending` 持续几分钟是正常的：拉源码几秒，装依赖 1 到 15 分钟，构建并推送镜像 1 到 3 分钟，滚动升级 30 秒到 2 分钟。默认等待预算 30 分钟，不要因为两分钟没结束就判定失败。超时退出时脚本会说明构建日志当时是否还在增长——还在增长就是慢，停止增长才可能是卡住。

脚本已内置的特殊情况，不需要手工处理：

- `CANNOT_DEPLOY_ONGOING_EXISTS`：自动从部署历史里找到 `status=pending` 的记录并跟踪；历史里没有 pending 时报错并交付冲突响应。
- `FILL_EXTRA_INFO`：记录提示并继续。
- `status=successful` 且 `error_detail` 为 `Rolling upgrade`：按成功处理。

超时只结束本地等待，平台侧仍在部署；继续轮询同一个 `deployment_id` 即可。

手工执行时依次调用 `deploy_with_module`、`get_deployment_result`（每 5 秒一次，直到 `successful` / `failed` / `interrupted`）、`list_processes`。`pending` 是进行中状态。

## 4. 成功交付

退出码 0 时脚本输出里已有 `url`。`url` 为空说明没有任何进程声明 `exposedType`，应用只能在集群内访问——这属于描述文件缺陷，报告并给出修复方案，不要当作成功交付。

输出里的 `processes.images` 是实例当前运行的镜像。交付前扫一眼：它应当带本次构建的时间戳，与上一次部署不同。

**首次部署要额外提醒用户。** 该环境第一次成功发布时 ingress 才刚建出来，路由规则生效有延迟，交付的 URL 在约一分钟内可能返回 502。这是正常现象，必须在交付 URL 时一并说明「稍等一分钟再访问」，否则用户会把它当成部署失败来回滚或重部。脚本判定是首次发布时会置 `first_release: true` 并在 `hint` 里给出这句话；重新部署已有环境不受影响，不要多余提示。

## 5. 部署成功但看不到变化

用户反馈“还是原来的”“怎么没变”时，先分清是没部署上去，还是部署上去了但用户看的不是它。按顺序排除：

| 检查 | 依据 | 说明 |
|---|---|---|
| 部署的是不是最新提交 | `git -C <repo> rev-parse HEAD` 对比输出里的 `version.revision` | 传了 `--repo-dir` 时脚本已经比过；手工部署时要自己比 |
| 远端有没有这个提交 | `git -C <repo> log origin/<branch> -1` | push 失败或推到了别的分支，远端就还是旧的 |
| 实例有没有换镜像 | 输出里的 `processes.images` | 镜像不变说明新构建没滚到实例上，多半是滚动升级还没走完 |
| 看的是不是同一个环境 | 输出里的 `url` | stag 与 prod 地址不同，用户常拿旧标签页对比 |

四项都对上还是看不到变化，就是浏览器或 CDN 缓存，让用户强制刷新。

需要整体重写代码时，正常提交即可：`git rm` 掉的文件会随提交在远端生效，不需要强推。只有本地历史与远端已经分叉、且用户明确要求丢弃远端历史时才用 `git push -f`；它不可逆地覆盖远端，执行前必须取得用户明确授权。

## 6. 失败诊断

进入本节时，再读取 [commands.md](../commands.md) 的“日志”“环境变量”和“增强服务”。

先按症状直接定位，命中就不必逐条翻日志：

| 症状 | 根因 | 修复 |
|---|---|---|
| 构建阶段失败 | 依赖装不上、`Dockerfile` 路径与模块 `dockerfile_path` 不符、buildpack 缺 `requirements.txt` / `runtime.txt` | 看 `logs` 里首个非零退出的命令 |
| buildpack 构建脚本报语法错误或工具链不支持 | 构建镜像里的语言版本低于本地，跑不动仓库的 build 脚本 | 本地构建好产物提交进仓库，同时移除 `package.json` 的 `build` 脚本，避免 buildpack 再跑一次 |
| `Pre-Release-Hook failed` | 钩子依赖的增强服务没绑定，或 migration 本身报错 | `list_module_services` 确认绑定，再看钩子输出 |
| 进程 `CrashLoopBackOff` | 启动命令错、二进制不在 `PATH`、必填环境变量缺失 | `list_processes` 的 `state_message` + 运行日志 |
| 进程 `ImagePullBackOff` | 镜像凭证或镜像地址问题 | 保留原始信息交给用户 |
| 进程长期 `Starting` 不就绪 | readiness 探针端口或路径与进程实际监听不符 | 对齐 `probes` 与 `services[].targetPort` |
| 部署成功但没有 `url` | 没有 `exposedType: bk/http` | 补 `services` 后重新部署 |
| 首次部署完成后一分钟内 502 | ingress 刚创建，路由规则还没生效 | 等一分钟重试；进程已就绪就不要改配置 |
| 有 `url` 但持续 502 | 进程监听端口与 `targetPort` 不一致，或进程绑定了 `127.0.0.1` / `localhost` | 对齐端口；绑定地址改成 `0.0.0.0` 或 `[::]` 后重新部署 |

症状不在表内时，按证据顺序收敛：

1. `get_deployment_result` 的 `logs`
2. `streams_history_events --channel_id <deployment_id>`
3. `list_processes`
4. `search_standard_log_with_post`
5. `list_config_vars`
6. `list_module_services`

先报告原因。用户同时要求修复时，改完仓库先重跑 `scripts/preflight.py`，再从“锁定版本”重新部署。

## 完成条件

- 成功：交付 `deployment_id`、`successful`、进程就绪计数与访问 URL；`first_release` 为真时附带 ingress 生效延迟的提醒。
- 部署成功但进程未就绪：交付 `deployment_id` 与 `processes.broken` 中的 `state` / `state_message`，标记为未就绪，不报成功。
- 失败或中断：交付 `deployment_id`、终态，以及定位到的原因。
- 预检未通过：交付 error 列表，标记为阻塞。
- 本地 HEAD 与平台 revision 不一致：交付两个 commit 与差异提交数，标记为阻塞，先让用户 push。
- 冲突且部署历史中没有 pending 记录：交付冲突响应与历史查询结果，标记为阻塞。
- 仍在 `pending`：继续等待；它不满足完成条件。
