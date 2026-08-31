---
name: bk-cli-paas-create
description: >-
  Creates BlueKing cloud-native / SaaS apps through `bk-cli paas
  create_cloud_native_app`. Use when the user wants to 创建云原生应用,
  创建 SaaS, 新建应用, 平台新建空白仓库, or 绑定已有工蜂仓库. Do not use
  for deploy, env vars, or 应用市场上架.
---

# bk-cli paas 新建

前置：读 [../bk-cli-paas/SKILL.md](../bk-cli-paas/SKILL.md) 的约定 / Guard / Flag。
写操作前：`../bk-cli-paas/scripts/guard.sh`。命令见 [../bk-cli-paas/commands.md](../bk-cli-paas/commands.md)。

只用 `bk-cli paas`。新建先复述 `code` / 仓库方式（已有 URL 或平台新建 `repo_name`）/ `build_method`，用户确认再 create。用户没说部署就停在创建成功。

## 1 已有仓库

对应控制台：创建应用 → 云原生 / 代码仓库 / 空模板 / **已有代码仓库** / 工蜂 / Dockerfile。

1. Guard；`get_minimal_app_list` 里已有 `code` → 交给 `bk-cli-paas-deploy`，不要再 create
2. 无 Procfile/app_desc → 按 Dockerfile `CMD` 补 `Procfile`（如 `web: python app.py`）并 push
3. `create_cloud_native_app`（`source_init_template=""`，`dockerfile_path=null`，URL 用工蜂 http/https clone 地址）
4. 有变量 / 中间件 → 交给 `bk-cli-paas-ops`；要部署 → `bk-cli-paas-deploy`

```bash
bk-cli paas create_cloud_native_app --body '{"is_plugin_app":false,"code":"bk-demo","name":"bk-demo","source_config":{"source_init_template":"","source_origin":1,"source_control_type":"tc_git","source_repo_url":"http://<host>/<org>/<repo>.git","source_dir":""},"bkapp_spec":{"build_config":{"build_method":"dockerfile","dockerfile_path":null,"docker_build_args":{}}}}'
```

空模板：`source_init_template=""`。已有仓库：`source_repo_url` 用工蜂 clone 地址（http/https 都行，不要 `git@`）。`dockerfile_path` 空则 `null`。buildpack 时才加 `source_init_template=dj2_with_auth`，去掉 `dockerfile_path` / `docker_build_args`。

create 报模板不可用 → `source_init_template` 改 `""`。`Invalid url` → 改 http/https clone URL，不要 SSH，也不要把已成功的 http 改成 https。已存在 → `bk-cli-paas-deploy`。
push 被 `committer-check` 拒 → 公司邮箱重 commit（不擅自改 git config）。

## 2 平台新建空白仓库

对应控制台：创建应用 → 云原生 / 代码仓库 / 空模板 / **新建代码仓库（由平台自动创建）** / 工蜂。

1. Guard；list 里已有 `code` → `bk-cli-paas-deploy`
2. `create_cloud_native_app`：`auto_create_repo=true`，`write_template_to_repo=false`，`repo_name` 默认等于 `code`，**不要传 `source_repo_url`**
3. 用户指定了工蜂项目组才加 `repo_group`；默认个人空间不传
4. 成功后 `get_app_info` 取 `modules[0].repo.repo_url`（如 `http://git.woa.com/<user>/<repo_name>.git`）
5. 仓库是空的，不能立刻部署：clone → 补 `Dockerfile` + `Procfile`/`app_desc.yaml` → push → `bk-cli-paas-deploy`

```bash
bk-cli paas create_cloud_native_app --body '{"is_plugin_app":false,"code":"bk-demo","name":"bk-demo","source_config":{"source_init_template":"","source_control_type":"tc_git","auto_create_repo":true,"write_template_to_repo":false,"repo_name":"bk-demo","source_origin":1,"source_dir":""},"bkapp_spec":{"build_config":{"build_method":"dockerfile","dockerfile_path":null,"docker_build_args":{}}}}'
```

空模板必须 `write_template_to_repo=false`。`write_template_to_repo=true` 还要非空模板名。`auto_create_repo` 时传了 `source_repo_url` 会校验失败。

## 加模块

```bash
bk-cli paas create_module --app_code bk-demo --body '{"name":"api","source_config":{"source_init_template":"","source_origin":1,"source_control_type":"tc_git","source_repo_url":"http://<host>/<org>/<repo>.git","source_dir":""},"bkapp_spec":{"build_config":{"build_method":"dockerfile","dockerfile_path":null,"docker_build_args":{}}}}'
```

模块名：小写字母/数字/连字符，最长 16。
