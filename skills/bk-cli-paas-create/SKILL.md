---
name: bk-cli-paas-create
description: >-
  Creates BlueKing cloud-native / SaaS apps through `bk-cli paas
  create_cloud_native_app`. Use when the user wants to 创建云原生应用,
  创建 SaaS, 新建应用, 平台新建空白仓库, or 绑定已有工蜂仓库.
---

# bk-cli paas 新建

前置：读 [../bk-cli-paas/SKILL.md](../bk-cli-paas/SKILL.md) 的约定 / Guard / Flag。
写操作前：`../bk-cli-paas/scripts/guard.sh`。命令见 [../bk-cli-paas/commands.md](../bk-cli-paas/commands.md)。

新建先复述 `code` / 仓库方式（已有 URL 或平台新建 `repo_name`）/ `build_method`，用户确认再 create。不要用 `get_minimal_app_list` 判断 code 是否空闲（列表看不到别人的应用）。create 报 code 已存在 → 把原文交用户换 code。用户没说部署就停在创建成功。

## 1 已有仓库

对应控制台：创建应用 → 云原生 / 代码仓库 / 空模板 / **已有代码仓库** / 工蜂 / Dockerfile。

1. Guard
2. 无 Procfile/app_desc → 按 Dockerfile `CMD` 的**实际命令**补独立 `Procfile`（见「Procfile 与 CMD」）并 SSH push
3. `create_cloud_native_app`（`source_init_template=""`，`dockerfile_path=null`，**API** 的 `source_repo_url` 用工蜂 http/https clone 地址，不要 `git@`）
4. 有变量 / 中间件 → 交给 `bk-cli-paas-ops`；要部署 → `bk-cli-paas-deploy`

```bash
bk-cli paas create_cloud_native_app --body '{"is_plugin_app":false,"code":"bk-demo","name":"bk-demo","source_config":{"source_init_template":"","source_origin":1,"source_control_type":"tc_git","source_repo_url":"http://<host>/<org>/<repo>.git","source_dir":""},"bkapp_spec":{"build_config":{"build_method":"dockerfile","dockerfile_path":null,"docker_build_args":{}}}}'
```

空模板：`source_init_template=""`。已有仓库：**API** 的 `source_repo_url` 用工蜂 http/https clone 地址，不要 `git@`。`dockerfile_path` 空则 `null`。buildpack 时才加 `source_init_template=dj2_with_auth`，去掉 `dockerfile_path` / `docker_build_args`。

create 报模板不可用 → `source_init_template` 改 `""`。`Invalid url` → **API** 改 http/https clone URL，不要把 `source_repo_url` 改成 SSH，也不要把已成功的 http 改成 https。create 报 code 已存在 → 把原文交用户换 code。
push 被 `committer-check` 拒 → 见「工蜂 git」。

## 2 平台新建空白仓库

对应控制台：创建应用 → 云原生 / 代码仓库 / 空模板 / **新建代码仓库（由平台自动创建）** / 工蜂。

1. Guard
2. `create_cloud_native_app`：`auto_create_repo=true`，`write_template_to_repo=false`，`repo_name` 默认等于 `code`，**不要传 `source_repo_url`**
3. 用户指定了工蜂项目组才加 `repo_group`；默认个人空间不传
4. 成功后 `get_app_info` 取 `modules[0].repo.repo_url`（如 `http://git.woa.com/<user>/<repo_name>.git`）
5. 仓库是空的，不能立刻部署：把 `repo_url` 转成 SSH 再 clone（见「工蜂 git」）→ 补 `Dockerfile` + `Procfile`/`app_desc.yaml` → 本地分支是 `main` 或 `master` 就 push 到**同名**远程分支 → `bk-cli-paas-deploy`（部署前必须 `get_repo_branches` 拿真实分支名和 `revision`）

```bash
bk-cli paas create_cloud_native_app --body '{"is_plugin_app":false,"code":"bk-demo","name":"bk-demo","source_config":{"source_init_template":"","source_control_type":"tc_git","auto_create_repo":true,"write_template_to_repo":false,"repo_name":"bk-demo","source_origin":1,"source_dir":""},"bkapp_spec":{"build_config":{"build_method":"dockerfile","dockerfile_path":null,"docker_build_args":{}}}}'
```

空模板必须 `write_template_to_repo=false`。`write_template_to_repo=true` 还要非空模板名。`auto_create_repo` 时传了 `source_repo_url` 会校验失败。

## 工蜂 git

`get_app_info` 的 `repo_url` 和 create 的 `source_repo_url` 是 http(s)，只给 **API** 用。本地 clone / push **优先 SSH**：

`http://git.woa.com/<user>/<repo>.git` → `git@git.woa.com:<user>/<repo>.git`

不要用 http(s) clone/push：环境常无 credential helper；全局 `url.*.insteadOf` 可能把 http 改写成 https，仍然 401。

push 被 `committer-check` 拒：拒绝原因只在 push 的 **remote 输出**里（常见：邮箱不属于工蜂账号），不要只看本地 git 摘要。公司邮箱格式：`<rtx>@tencent.com`。rtx 不确定就问用户，不要猜。不擅自改 git config。重 commit（只改这一笔）：

```bash
GIT_COMMITTER_NAME="<当前 name>" GIT_COMMITTER_EMAIL="<rtx>@tencent.com" git commit --amend --author="<当前 name> <rtx>@tencent.com" --no-edit
```

不要同时用 `--reset-author` 和 `--author`。name 用当前 committer，只改邮箱。

## Procfile 与 CMD

两者并存。`CMD` 是正常容器命令；`Procfile` 是仓库根单独文件、一行 `web: <cmd>`。不要把 Procfile 语法写进 `CMD`。应用必须且只能监听 **5000**。

```dockerfile
# 对
CMD ["python", "app.py"]
# 错：CMD ["web: python app.py"]
```

```
# Procfile
web: python app.py
```

## 加模块

```bash
bk-cli paas create_module --app_code bk-demo --body '{"name":"api","source_config":{"source_init_template":"","source_origin":1,"source_control_type":"tc_git","source_repo_url":"http://<host>/<org>/<repo>.git","source_dir":""},"bkapp_spec":{"build_config":{"build_method":"dockerfile","dockerfile_path":null,"docker_build_args":{}}}}'
```

模块名：小写字母/数字/连字符，最长 16。
