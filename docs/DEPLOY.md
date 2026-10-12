# 部署指南（v2.2 K7）

本文档说明如何在单台机器上用 Docker Compose 和 Caddy 部署 SourceHero。每条命令都标明了在哪里执行过：

- **已在本机执行**：`docker compose ... config -q`、`docker compose --profile app build`、`make backup`、`make restore`、`make loadtest`、`make dev`、`make demo`。
- **未在本机执行**：`docker compose ... up -d`。它会占用与开发栈相同的端口，需要在真正的服务器上执行。

## 1. 准备

1. 安装 Docker 和 Docker Compose v2（需要支持 `env_file` 的 `required: false` 语法，即 Compose 2.24 或更新版本）。
2. 复制配置文件：`cp .env.example .env`。**不要**在生产中保留示例里的开发口令（`sourcehero_dev`）。
3. 生成密钥并写入 `.env` 的 `SOURCEHERO_SECRET_KEY`（至少 32 字节，生产守卫会拒绝默认值）：

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

4. 设置数据库口令 `POSTGRES_PASSWORD`。compose 要求它必填，并且它必须与 `DATABASE_URL` 中的密码一致。
5. 设置生产开关：`SOURCEHERO_ENV=production`、`PUBLIC_WEB_URL=https://<你的域名>`、`SITE_ADDRESS=<你的域名>`。`ALLOW_REGISTRATION` 不设置时，生产环境默认为 `false`；`DOCS_ENABLED` 不设置时，生产环境默认关闭 `/docs` 与 `/openapi.json`。

校验配置（已执行，退出码 0）：

```bash
POSTGRES_PASSWORD=<你的口令> docker compose --profile app --profile prod config -q
```

## 2. 构建与启动

构建镜像（已执行，退出码 0）：

```bash
POSTGRES_PASSWORD=<你的口令> docker compose --profile app build
```

Web 镜像的 `NEXT_PUBLIC_API_URL` 是构建参数。compose 无法按 profile 为它设置不同的值，所以在生产中要在构建前导出它，让前端经 Caddy 使用同源的 `/api`：

```bash
export NEXT_PUBLIC_API_URL=/api
```

启动（未在本机执行）：

```bash
docker compose --profile app --profile prod up -d
```

`prod` profile 中的 Caddy 监听 80 和 443。`deploy/Caddyfile` 把 `/api/*` 转发给 API，其余请求转发给 Web。Caddy 会在 `SITE_ADDRESS` 为公网域名时自动申请 TLS 证书，前提是 80 和 443 端口可以从公网访问。

## 3. 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `SOURCEHERO_ENV` | 是 | `production` 启用生产守卫 |
| `SOURCEHERO_SECRET_KEY` | 生产必填 | JWT 与加密的密钥，至少 32 字节，不能是默认值 |
| `POSTGRES_PASSWORD` | 是（compose） | 数据库容器的口令，须与 `DATABASE_URL` 一致 |
| `DATABASE_URL` | 是 | 生产中密码不能是 `sourcehero_dev` |
| `PUBLIC_WEB_URL` | 生产必填 | 公网地址，`https://` 开头时才会发送 HSTS |
| `SITE_ADDRESS` | 使用 Caddy 时必填 | 证书申请的域名 |
| `ALLOW_REGISTRATION` | 否 | 生产默认 `false`，只有显式设置才会开启 |
| `DOCS_ENABLED` | 否 | 生产默认关闭 `/docs`，设为 `true` 才开启 |
| `JWT_TTL_HOURS` | 否 | 令牌有效期，默认 24 |
| `LOG_FORMAT` | 否 | `text` 或 `json`；容器镜像默认 `json` |
| `METRICS_TOKEN` | 否 | 为空时 `/metrics` 返回 404；设置后需要 Bearer 令牌 |
| `DB_POOL_SIZE`、`DB_MAX_OVERFLOW` | 否 | 数据库连接池大小，默认 5 与 10 |
| `RATE_LIMIT_ENABLED` | 否 | 默认关闭；开启后下列阈值生效 |
| `RATE_LIMIT_WINDOW_SECONDS` 与 `RATE_LIMIT_LOGIN` / `ASK` / `VERIFY` / `CAPTURE` / `UPLOAD` | 否 | 固定窗口的时长与每窗口次数 |
| `LLM_PROVIDER`、`LLM_PRESET`、`LLM_API_KEY`（或 `LLM_AUTH`、`LLM_TOKEN_COMMAND`、OAuth 变量） | 否 | 见第 8 节 |
| `LLM_DAILY_TOKEN_BUDGET`、`LLM_DAILY_REQUEST_BUDGET` | 否 | 每日预算，0 表示不限 |
| `EMBEDDING_PROVIDER`、`EMBEDDING_MODEL`、`EMBEDDING_DIM` | 否 | 见第 9 节 |
| `FETCH_ALLOW_PRIVATE` | 否 | 默认 `false`，抓取时拒绝私网地址（SSRF 防护） |

完整清单以 `.env.example` 为准。

## 4. 创建首个管理员

生产环境默认关闭注册。第一个成功注册的用户会自动成为管理员（见 `app/api/routers/auth.py`），因此：

1. 临时设置 `ALLOW_REGISTRATION=true` 并重启 API；
2. 通过 Web 注册第一个账号；它会成为管理员；
3. 把 `ALLOW_REGISTRATION` 改回 `false` 并重启 API。

`app.demo.seed` 创建的是普通用户，不是管理员。

## 5. 备份与恢复

备份（已执行，`make backup`）：

```bash
make backup
```

它把数据库做成 `pg_dump -Fc` 文件，并把本地存储目录打成 tar，写入 `backups/<UTC 时间戳>/`，同时生成 `MANIFEST.txt`（含 sha256，不含任何口令）。`backups/` 已加入 `.gitignore`。数据库连接从 `.env` 读取；本机没有 `pg_dump` 时，脚本会改用正在运行的 Postgres 容器里的 `pg_dump`。

恢复（已执行，`make restore`，恢复到一个新库）：

```bash
make restore ARGS="backups/<时间戳> sourcehero_restore_check"
```

恢复脚本拒绝覆盖主库，除非加上 `--force`。`--force` 同时会把 tar 解压回存储目录。自动化的往返测试在 `tests/test_backup_restore.py`（标记为 `slow` 和 `db`），它比较核心表的行数和迁移版本。

## 6. 升级与迁移

- 镜像内的入口脚本在 `RUN_MIGRATIONS=1` 时先执行 `alembic upgrade head`，再启动进程。
- 本机也可以手动执行：`make migrate`。
- 当前迁移链：`0009_revision_method`（修订记录编译方式）→ `0010_security`（用户的 `token_version` 与限流表）。

## 7. 可观测性

- **日志**：`LOG_FORMAT=json` 时，每行是一条 JSON，包含 `request_id`。每个请求输出一行 access 日志：方法、路由模板（不是原始路径）、状态码、耗时（毫秒），以及用户 id 的 sha256 前 12 位。调用方可以通过 `X-Request-ID` 传入 request id。
- **指标**：`GET /metrics`。`METRICS_TOKEN` 为空时返回 404；设置后需要 `Authorization: Bearer <METRICS_TOKEN>`，错误的令牌返回 401。
- **不要通过 Caddy 抓取指标**：Caddy 只转发 `/api/*` 和 Web 请求，`/metrics` 不在其中。Prometheus 应直接访问 API 容器的 `api:8000/metrics`，放在同一个内部网络里。
- **健康检查**：`/healthz`（存活）、`/readyz`（就绪）。worker 的容器健康检查执行 `python -m app.jobs.worker --healthcheck`：数据库可连接，并且心跳文件 `DATA_DIR/worker.heartbeat` 在 120 秒以内更新过，才返回 0。

## 8. LLM 配置

- 预设 `openrouter`：只允许免费模型（模型 id 以 `:free` 结尾，或为 `openrouter/free`）。设置页会提示这一限制。
- `LLM_ALLOW_PAID` 默认 `false`。
- 每日的 token 与请求预算由 `LLM_DAILY_TOKEN_BUDGET`、`LLM_DAILY_REQUEST_BUDGET` 控制；设置页显示今日已用量和上限。
- token command 可以设置自己的 TTL（保存在凭据里，范围 0–86400 秒）；未设置时使用 `LLM_TOKEN_TTL_SECONDS`。

## 9. embedder 切换与重建索引

- `EMBEDDING_PROVIDER` 可选 `hashing`（默认，离线）、`fastembed`（本地模型）、`openai_compat`。
- 切换后需要为已有文档重建向量：登录后调用 `POST /v1/index/reindex`（作为后台任务排队执行）。

## 10. 负载

K7.7 的负载冒烟结果、机器信息和命令见 `docs/dev/LOADTEST.md`。

## 11. 已知限制

- 周计划（weekly）无法通过 PATCH 改回每天，界面会禁用这一选项并提示删除后重建（重建会丢失运行历史）。
- 登录限流按邮箱计数，因此任何人都可以让某个已知邮箱暂时被锁定；这是规格要求的行为，尚未加入按 IP 的第二维度。
- wiki 的阵营标注不处理跨簇的冲突，详见 `docs/dev/DECISIONS.md`。
