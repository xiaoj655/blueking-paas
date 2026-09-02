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

仓库根需要 `Procfile` 或 `app_desc.yaml`，Dockerfile 应用也一样；Dockerfile 构建还需要 `Dockerfile`。已有 `app_desc.yaml` 时直接使用；新补描述文件时优先 `app_desc.yaml`。

`Dockerfile` 的 `CMD` 保持容器命令，例如 `CMD ["python", "app.py"]`；`Procfile` 单独写 `web: <cmd>`。进程只监听端口 `5000`。

## 页面呈现

由本任务生成应用代码且应用带 Web 界面时，采用前后端分离：后端只提供 JSON API，页面是独立前端；构建产物由后端进程托管，进程仍只监听 `5000`。

样式推荐 Tailwind，经 CDN 引入。运行环境访问不到公网时改为随仓库提交样式文件，不交付样式加载失败的页面。

## 2. 使用已有仓库

1. 按“应用与模块”的已有仓库命令创建。
2. 从 API 返回数据核对 code 与默认模块。
3. 用户还要求准备代码或部署时，检查仓库根的部署描述文件。缺失时，根据应用实际启动命令生成；Dockerfile 构建从 `CMD` 取命令。然后通过 SSH push。

本分支以 API 返回成功完成；用户还要求部署时，仓库同时满足“仓库部署约束”。

## 3. 平台新建空白仓库

1. 使用 `auto_create_repo=true`、`write_template_to_repo=false` 并省略 `source_repo_url` 创建。`repo_name` 默认等于 `code`；仅用户指定工蜂项目组时加入 `repo_group`。
2. 用 `get_app_info` 读取 `modules[0].repo.repo_url`。
3. 用户还要求准备代码或部署时，将 URL 转成 SSH，clone 后补 `Procfile`/`app_desc.yaml`；Dockerfile 构建同时补 `Dockerfile`。再把本地 `main` 或 `master` push 到同名远程分支。

本分支以 API 返回成功完成；用户还要求部署时，代码必须已 push 且仓库满足“仓库部署约束”。只要求创建时，平台仓库保持为空。

## 4. 添加模块

按“应用与模块”的 `create_module` 命令执行。模块名仅含小写字母、数字与连字符，最长 16 个字符。

本分支以 API 返回成功且模块出现在返回数据中完成。

## 失败分支

- `Invalid url`：API 的 `source_repo_url` 改为工蜂 HTTP/HTTPS clone URL。
- 模板不可用：保持 `source_init_template=""`。
- code 已存在：请用户提供新 code。
- push 被 `committer-check` 拒绝：执行“工蜂 Git”的处理步骤。
