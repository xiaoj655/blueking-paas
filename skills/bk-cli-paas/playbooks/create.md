# 创建应用与模块

执行前读取 [commands.md](../commands.md) 的“应用与模块”；需要 clone 或 push 时再读“工蜂 Git”。

## 1. 锁定创建参数

复述并请用户确认：

1. `code`
2. 仓库方式：已有仓库 URL，或平台新建仓库的 `repo_name`
3. `build_method`

默认值只填用户未指定的项：

| 项 | 默认值 |
|---|---|
| `source_control_type` | `tc_git` |
| `source_origin` | `1`（代码仓库） |
| `build_method` | `dockerfile`；仅用户指定时使用 buildpack |
| `source_init_template` | `""`（空模板） |
| 仓库方式 | 已有仓库 |
| `source_dir` | `""`（仓库根） |
| `dockerfile_path` | `null`（构建目录下的 `Dockerfile`） |
| `docker_build_args` | `{}` |
| `is_plugin_app` | `false` |

Dockerfile 构建的 `source_init_template` 使用空字符串；内部模板名 `docker` 不是可用 API 值。buildpack 的字段组合以“应用与模块”为准。

已有仓库的 API URL 与本地 Git URL 按“工蜂 Git”选取。创建 body 省略 `custom_image`、`source_origin=6`、github/bare_git、`region`、网关 `--stage` 与 `app_tenant_mode`。

参数经用户确认后本节完成。code 是否可用以创建 API 的响应为准；`get_minimal_app_list` 只列调用方可见应用。

## 仓库部署约束

生成或修改部署描述文件前先读 [app_desc.yaml 规范](../references/app-desc.md)，它给出经源码核验的字段白名单和两份可直接套用的模板。

仓库根需要 `app_desc.yaml`，Dockerfile 应用也一样。已有描述文件时直接沿用，不要重写。新补时只写 `app_desc.yaml`，不要同时写 `Procfile`——两者进程定义不一致会让部署直接报 `Process definitions conflict`。

四条约束决定应用能否真正跑起来：

1. 进程命令写在 `spec.processes[].procCommand`。这是平台实际执行的命令，Dockerfile 的 `CMD` 不参与云原生应用的进程编排——operator 直接用 `procCommand` 拆出的 command/args 覆盖容器入口。Dockerfile 只负责产出可执行文件和资源，命令入口放在 `PATH` 里。
2. 每个进程监听 5000，`services[].targetPort` 与探针端口都写 5000；监听地址用 `0.0.0.0` 或 `[::]`，绑到 `127.0.0.1` 或 `localhost` 会让探针不过、入口一直 502。
3. web 进程必须带 `services[].exposedType.name: bk/http`，否则部署会成功但没有访问地址。一个模块只能有一个 `exposedType`。
4. 模块必须声明 `language`，`processes` 和 `hooks` 都放在 `spec` 下，不能直接挂在模块层。

按构建方式补齐仓库文件：

| 构建方式 | 必需文件 |
|---|---|
| dockerfile | `app_desc.yaml`、`Dockerfile`（路径需与模块 `dockerfile_path` 一致） |
| buildpack | `app_desc.yaml`，以及语言运行时声明：Python 用 `requirements.txt` + `runtime.txt`，Node 用 `package.json`，Go 用 `go.mod`；需要系统包时补 `Aptfile` |

push 前运行校验，它在本地拦截会导致部署失败或部署成功却不可用的写法：

```bash
python3 scripts/preflight.py --repo-dir <仓库路径> --module <module>
```

本节以 `preflight ok`（0 errors）完成。仍有 error 时先修复，不要 push。

## 页面呈现

由本任务生成应用代码且应用带 Web 界面时，采用前后端分离：后端只提供 JSON API，页面是独立前端；构建产物由后端进程托管，进程仍只监听 `0.0.0.0:5000`。

样式推荐 Tailwind，经 CDN 引入。运行环境访问不到公网时改为随仓库提交样式文件，不交付样式加载失败的页面。

## 2. 使用已有仓库

1. 按“应用与模块”的已有仓库命令创建。
2. 从 API 返回数据核对 code 与默认模块。
3. 用户还要求准备代码或部署时，检查仓库根的部署描述文件。缺失时按“仓库部署约束”生成，`procCommand` 取应用实际启动命令；已有 `Dockerfile` 且带 `CMD` 时，把该命令搬到 `procCommand`。跑通 preflight 后再通过 SSH push。

本分支以 API 返回成功完成；用户还要求部署时，仓库同时满足“仓库部署约束”。

## 3. 平台新建空白仓库

1. 使用 `auto_create_repo=true`、`write_template_to_repo=false` 并省略 `source_repo_url` 创建。`repo_name` 默认等于 `code`；仅用户指定工蜂项目组时加入 `repo_group`。
2. 用 `get_app_info` 读取 `modules[0].repo.repo_url`。
3. 用户还要求准备代码或部署时，将 URL 转成 SSH，clone 后按“仓库部署约束”补齐文件并跑通 preflight。再把本地 `main` 或 `master` push 到同名远程分支。

本分支以 API 返回成功完成；用户还要求部署时，代码必须已 push 且仓库满足“仓库部署约束”。只要求创建时，平台仓库保持为空。

## 4. 添加模块

按“应用与模块”的 `create_module` 命令执行。模块名仅含小写字母、数字与连字符，最长 16 个字符。

本分支以 API 返回成功且模块出现在返回数据中完成。

## 失败分支

- `Invalid url`：API 的 `source_repo_url` 改为工蜂 HTTP/HTTPS clone URL。
- 模板不可用：保持 `source_init_template=""`。
- code 已存在：请用户提供新 code。
- push 被 `committer-check` 拒绝：执行“工蜂 Git”的处理步骤。
