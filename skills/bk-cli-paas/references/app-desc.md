# app_desc.yaml（specVersion 3）

生成或修改部署描述文件时必读。字段以 apiserver 的校验器为准：`paasng/platform/bkapp_model/serializers/v1alpha2.py`（`BkAppSpecInputSLZ`）。不在白名单里的字段一律不写。

## 1. 六条硬约束

违反这六条会导致部署失败，或部署“成功”但应用不可用。写完描述文件必须逐条对照。

1. **进程监听 5000，并绑定 `0.0.0.0` 或 `[::]`。** 平台注入 `PORT=5000`（`CONTAINER_PORT` 默认值），`services[].targetPort` 与探针端口都要等于进程实际监听的端口。端口不一致时部署状态是 `successful`，但入口 502、readiness 永远不就绪。绑定 `127.0.0.1` 或 `localhost` 是同一类故障：回环地址只有容器自己连得上，kubelet 探针与 Service 都到不了，现象同样是部署成功而入口 502。
2. **specVersion 3 不会自动补 `services`。** v1/v2 与纯 Procfile 由平台补默认 service，v3 只认显式声明（`bkapp_model/services.py` 的 `upsert_proc_svc_by_spec_version`）。web 进程缺少 `services[].exposedType.name: bk/http` 时不创建集群外 ingress，部署成功但拿不到访问地址。
3. **一个模块最多一个 `exposedType`**，且 `bk/http` 与 `bk/grpc` 不能共存。多个进程都写 `exposedType` 会在校验阶段直接报 `duplicate exposedType`。
4. **进程名 ≤ 12 字符**，匹配 `^[a-z0-9]([-a-z0-9])*$`（`paasng/utils/validators.py:124`）。`worker`、`web`、`scheduler` 合法；`celery-worker`(13) 超长。
5. **`spec.addons` 在仓库部署路径是空操作。** `entities_syncer/addons.py` 的 `sync_addons` 只打一条 warning 就返回，不会创建或绑定增强服务。需要 MySQL 等服务时必须先用 `scripts/bind_service.py` 绑定，再部署。写了 `preRelease: migrate` 却没绑 MySQL，部署一定挂在 Pre-Release-Hook。
6. **模块必须声明 `language`，进程必须写在 `spec` 下。** 部署路径的 `DeploymentDescSLZ`（`declarative/deployment/validations/v3.py`）要求 `language` 与 `spec` 必填，`language` 取 `Python` `PHP` `Go` `NodeJS` `Java`，大小写不敏感。模块层只读 `name` `language` `sourceDir` `isDefault` `spec` 五个键，其余静默丢弃——把 `processes` 直接挂在模块下而不是 `spec` 下，就是这样丢掉的。

## 2. 字段白名单

`spec` 下经校验器接受的字段：

| 字段 | 说明 |
|---|---|
| `processes[]` | `name` `procCommand` `command` `args` `replicas` `resQuotaPlan` `services` `probes` `autoscaling` `gracefulShutdownSeconds` |
| `processes[].services[]` | `name`(DNS label,≤63) `targetPort`(整数或字符串 `${PORT}`) `port` `protocol`(TCP/UDP) `exposedType.name`(bk/http、bk/grpc) |
| `hooks.preRelease` | `procCommand` 或 `command`+`args` |
| `configuration.env[]` | `name`(大写下划线) `value` `description` |
| `envOverlay` | `envVariables[]` `replicas[]` `resQuotas[]` `autoscaling[]` `mounts[]`，每项用 `envName: stag\|prod` |
| `svcDiscovery.bkSaaS[]` | `bkAppCode` `moduleName`，注入 `BKPAAS_SERVICE_ADDRESSES_BKSAAS` |
| `observability.monitoring.metrics[]` | `process` `serviceName` `path` `params`；`serviceName` 必须命中该进程的 `services[].name` |
| `build` | 只有 `image` `imagePullPolicy` `imageCredentialsName` |

`resQuotaPlan` 取平台内置方案：`default`(4C1G) `1C1G` `2C1G` `2C2G` `4C1G` `4C2G` `4C4G`。未知值报 `Resource quota plan 'X' does not exist or is inactive.`

### 不要写的字段

| 写了会怎样 | 字段 |
|---|---|
| 校验器无此字段，静默丢弃，误以为生效 | `build.dockerfile`、`build.buildTarget`、`build.args`、`build.buildpacks` |
| 通过校验但不执行任何绑定 | `spec.addons` |
| 部署路径不读取 | 根级 `app`、`market`（仅 S-mart 包需要） |
| 已废弃，改用 `services[].targetPort` | `processes[].targetPort` |

Dockerfile 路径不由描述文件决定，取模块创建时 `bkapp_spec.build_config.dockerfile_path` 的值。要改路径就改模块配置，不是改 YAML。

## 3. 单模块简写

部署路径同时接受两种写法，仓库根的单模块应用推荐 `module`：

```yaml
specVersion: 3
module:
  language: Python
  spec: {}
```

多模块必须用 `modules` 列表，每项带 `name`，且恰好一个 `isDefault: true`。v3 的 `modules` 只能是列表，写成字典会报“模块格式不正确, 期望类型: list”。

## 4. 模板：Dockerfile 构建

取自可部署的 Go 应用 `loong-test-922`。要点是 Dockerfile 只负责产出二进制与资源，**不写 `CMD`**；进程由 `procCommand` 驱动。

```yaml
specVersion: 3
appVersion: 1.0.0
modules:
  - name: default
    isDefault: true
    language: Go
    spec:
      hooks:
        preRelease:
          procCommand: "myapp migrate"
      processes:
        - name: web
          resQuotaPlan: default
          procCommand: "myapp webserver"
          services:
            - name: web
              targetPort: 5000
              protocol: TCP
              exposedType:
                name: bk/http
          probes:
            liveness:
              httpGet:
                port: 5000
                path: "/ping"
              initialDelaySeconds: 5
              timeoutSeconds: 3
              periodSeconds: 30
              failureThreshold: 3
            readiness:
              httpGet:
                port: 5000
                path: "/ping"
              initialDelaySeconds: 5
              timeoutSeconds: 3
              periodSeconds: 5
              failureThreshold: 3
        - name: scheduler
          replicas: 1
          resQuotaPlan: default
          procCommand: "myapp scheduler"
```

配套 Dockerfile 的骨架：

```dockerfile
FROM golang:1.25-alpine AS builder
WORKDIR /go/src/
COPY go.mod go.sum ./
RUN go env -w GOPROXY=https://mirrors.cloud.tencent.com/go/,direct
RUN go mod download
COPY . .
RUN make build

FROM alpine:3.22 AS runner
WORKDIR /app
COPY --from=builder /go/src/myapp /usr/bin/myapp
COPY --from=builder /go/src/templates /app/templates
COPY --from=builder /go/src/static /app/static
ENV TMPL_FILE_BASE_DIR=/app/templates
ENV STATIC_FILE_BASE_DIR=/app/static
```

二进制要放进 `PATH`（`/usr/bin/`），否则 `procCommand: "myapp webserver"` 会 `command not found`。静态资源、模板、i18n 用环境变量指定绝对路径，不要依赖相对 `WORKDIR` 的相对路径。

## 5. 模板：buildpack 构建

取自可部署的 Django 应用 `loong-test-8-7`。

```yaml
specVersion: 3
module:
  language: Python
  spec:
    processes:
      - name: web
        procCommand: gunicorn wsgi -w 4 -b [::]:5000 --access-logfile - --error-logfile -
        services:
          - name: web
            exposedType:
              name: bk/http
            targetPort: 5000
            port: 80
    hooks:
      preRelease:
        procCommand: "python manage.py migrate --no-input"
```

buildpack 构建靠仓库文件声明运行时，缺一个就构建失败：

| 文件 | 内容 | 缺失后果 |
|---|---|---|
| `runtime.txt` | `python-3.10.5` | 用默认版本，依赖可能装不上 |
| `requirements.txt` | pip 依赖 | 构建失败 |
| `Aptfile` | 系统包，如 `default-libmysqlclient-dev` | 编译 mysqlclient 一类扩展时构建失败 |

## 6. Procfile

`Procfile` 仍受支持，但和 `app_desc.yaml` 并存时，两边的进程定义必须完全一致，否则报 `Process definitions conflict between Procfile and app description file`。新建仓库只写 `app_desc.yaml`，不要两个都写。

只有 `Procfile` 时走 v1 路径，平台会自动补默认 service 并暴露 web 进程；这是唯一不需要显式写 `services` 的情况，但也拿不到探针与分环境配置。
