# 准备、认证与 403

## 1. 建立可用客户端

本机没有 `bk-cli`，或 `--dry-run` 指向 `bkpaas3` 时，使用腾讯内网构建。npm 与 GitHub release 二进制没有注入 `BK_TE_DOMAIN`。

用户明确要求安装或修复客户端时可直接执行；若安装只是其他任务的隐式前置条件，先说明脚本会 clone/pull `DIR` 并覆盖 `PREFIX/bin/bk-cli`，取得确认后运行：

```bash
bash scripts/install.sh
export PATH="$HOME/.local/bin:$PATH"
```

脚本需要 Go，默认使用 `DIR=$HOME/repo/bk-cli`、`PREFIX=$HOME/.local`、`BK_TE_DOMAIN=o.woa.com`。它会 clone 或 `--ff-only` pull、`make build` 并安装；已有源码目录时通过 `DIR` 指定。

仅在本机没有可用 context 时初始化：

```bash
bk-cli context init --bk_api_url_tmpl="https://{gateway_name}.apigw.o.woa.com"
```

`{gateway_name}` 是原样保留的模板占位符。已有 context 直接复用。

本节以 `bk-cli` 可执行且 `get_minimal_app_list --dry-run` 返回请求 URL 完成。

## 2. 通过 Guard

运行：

```bash
scripts/guard.sh
```

`auth check` 只确认本地存在凭证；Guard 还会用 `get_minimal_app_list` 验证 token。缺少或失效时，请用户用其提供的凭证登录：

```bash
bk-cli auth login --access_token="<user-provided>"
bk-cli auth login --bk_app_code="<code>" --bk_app_secret="<secret>" --bk_token="<token>"
```

凭证值必须由用户提供。登录后重新运行 Guard。本节以输出 `guard ok` 完成；若失败被归类为 403，则进入下一节。

## 3. 收敛 403

先用 `get_minimal_app_list --dry-run` 检查请求 URL：

- URL 含 `bkpaas3` 且返回全员 403 / `1640301`：运行 `scripts/install.sh` 注入 `BK_TE_DOMAIN`，目标是 `paasv3`；这不是 bkpaas3 API 权限问题。
- URL 已含 `paasv3` 且返回 `1640301`：为调用方 `app_code` 申请对应 API 权限。
- IAM 错误 `9900403`：按业务权限处理。

处理后重新运行 Guard。准备阶段只以 `guard ok` 完成；仍失败则保持未完成。
