# SourceHero v2.2.0rc1 发布说明（运维）

> 面向部署和运维人员。变更总览见 [CHANGELOG.md](../../CHANGELOG.md)；部署步骤见 [docs/DEPLOY.md](../DEPLOY.md)。

---

## 1. 升级步骤

1. **先备份。** `make backup`（`scripts/backup.sh`）。确认生成的转储文件存在且非空。恢复用 `make restore ARGS=...`（`scripts/restore.sh` 拒绝覆盖主库，`--force` 才会解压存储目录）。往返测试：`tests/test_backup_restore.py::test_backup_then_restore_matches_source`。
2. **更新代码与 `.env`。** 对照 `.env.example` 补充第 2 节列出的变量。
3. **执行迁移：** `make migrate`（即 `alembic upgrade head`）。v2.2 新增三步迁移，都只做加列或建表，不改动已有数据：

   | 迁移 | 变更 |
   |---|---|
   | `0009_revision_method` | `wiki_page_revisions.compile_method` 可空字符串列。迁移前的旧修订该列为空，界面不显示 |
   | `0010_security` | `users.token_version` 整数列（默认 0）；新表 `rate_limits`（主键 `key, window_start`） |
   | `0011_features` | `sources.reliability`（默认 `unknown`，CHECK 限制为 unknown/low/medium/high）；`sources.reliability_note`；新表 `share_links`（只存 token 的 sha256） |

   每一步都可以单独回退，`tests/test_migration_roundtrip.py::test_each_step_upgrades_and_downgrades_its_own_schema` 逐步验证。
4. **生产部署。** `POSTGRES_PASSWORD` 在 `docker-compose.yml` 中是必填项（`${POSTGRES_PASSWORD:?...}`），未设置时 compose 直接报错退出（OPS-9）。Web 镜像在构建期读取 `NEXT_PUBLIC_API_URL`，同源部署应设为 `/api`，否则需要重新构建。
5. **升级后重新登录。** 见第 3 节的 JWT 说明。

---

## 2. 新增环境变量

以下变量相对 v2.1.0-rc1 的 `.env.example` 新增（`git diff v2.1.0-rc1 -- .env.example` 可复核）。默认值来自 `app/core/config.py`。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `LOG_FORMAT` | `text` | `json` 输出结构化日志，容器与 compose 中设为 `json` |
| `METRICS_TOKEN` | 空 | `/metrics` 的 bearer 令牌；为空时 `/metrics` 返回 404（`tests/test_metrics.py`） |
| `DOCS_ENABLED` | 开发环境 `true`；生产环境 `false` | 是否开放 `/docs` 与 `/openapi.json`；生产环境只有显式设置才开放（SEC-18） |
| `DB_POOL_SIZE` | `5` | 每个进程的 SQLAlchemy 连接池大小 |
| `DB_MAX_OVERFLOW` | `10` | 连接池之上允许的额外连接数 |
| `RATE_LIMIT_ENABLED` | `false` | 是否启用 Postgres 固定窗口限流（SEC-20） |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | 窗口长度（秒） |
| `RATE_LIMIT_LOGIN` | `10` | 每个窗口内的登录次数（按邮箱计数） |
| `RATE_LIMIT_ASK` | `30` | 每个窗口内的 ask 次数（按用户） |
| `RATE_LIMIT_VERIFY` | `30` | 每个窗口内的 verify 次数；公开分享读取也复用此值（按客户端主机） |
| `RATE_LIMIT_CAPTURE` | `60` | 每个窗口内的 capture 次数 |
| `RATE_LIMIT_UPLOAD` | `20` | 每个窗口内的上传次数 |
| `SHARING_ENABLED` | `true` | `false` 时所有分享路由（包括公开读取）返回 404 |
| `POSTGRES_PASSWORD` | 无（必填） | compose 中数据库容器的密码，须与 `DATABASE_URL` 一致 |
| `LLM_TOKEN_TTL_SECONDS` | `300` | command 类型凭据未单独设置 TTL 时的默认值 |
| `LLM_DAILY_REQUEST_BUDGET` | `0`（不限）；openrouter 预设默认 `800` | 全实例每个 UTC 日的请求次数上限，超出时直接回退，不调用模型 |
| `LLM_CASSETTE_MODE` | `off` | `record` 或 `replay`，用于录制与离线回放真实模型结果 |
| `LLM_CASSETTE_PATH` | 空 | cassette 文件路径 |
| `LLM_EXTRA_BODY` | 空 | 合并进每个请求体的 JSON 对象 |
| `LLM_ALLOW_PAID` | `false` | 设为 `true` 才允许非 `:free` 的付费模型。本项目禁止设置 |
| `OPENROUTER_API_KEY` | 空 | OpenRouter 密钥，仅保存在本地 `.env` 中，不得提交（`tests/test_no_secrets.py`） |
| `RERANK_PROVIDER` | `none` | `fastembed` 时启用重排模型 |
| `RERANK_MODEL` | `Xenova/ms-marco-MiniLM-L-6-v2` | 重排模型名 |
| `RETRIEVAL_CANDIDATES` | `50` | 重排前每路召回的候选数 |
| `RRF_K` | `60` | 倒数排名融合的常数 k |

同时，`JWT_TTL_HOURS` 的默认值由 168 改为 24（见第 3 节），`.env.example` 中的该项需要同步检查。

---

## 3. 行为变化

v2.2 没有删除或改名任何 v2.1 的 API 路径（对比 v2.1.0-rc1 的 `dashboard-web/openapi.json`：66 个路径全部保留，现为 85 个）。下列变化不会以"破坏性变更"的形式出现在 API 结构中，但运维和客户端需要知道：

1. **JWT 有效期** 从 168 小时改为 **24 小时**（`app/core/config.py` 的 `jwt_ttl_hours`；SEC-19）。
2. **升级后旧 JWT 很可能失效。** 新版本要求 JWT 带 `token_version` 声明，`tests/test_auth.py::test_jwt_without_token_version_claim_is_rejected` 表明缺少该声明的 JWT 会被拒绝。升级前签发的 JWT 因此应当需要重新登录。这一点是从测试推断的，请在预发环境确认。个人访问令牌（PAT）不受影响。
3. **生产环境默认关闭注册。** `ALLOW_REGISTRATION` 在生产环境未显式设置时为 `false`（`tests/test_production_guard.py::test_registration_defaults_to_off_in_production`）。此前第一个注册者会自动成为管理员，这一行为在生产环境已取消。
4. **生产环境拒绝弱配置启动。** secret 短于 32 字节（按 UTF-8 字节计）或数据库仍为开发密码时，应用拒绝启动（`tests/test_production_guard.py::test_production_refuses_a_secret_shorter_than_32_bytes`、`::test_production_refuses_the_development_database_password`）。
5. **登录限流** 改为 Postgres 计数，并且**计每一次尝试**，不再只计失败。它按邮箱计数，并且默认关闭，需要 `RATE_LIMIT_ENABLED=true`。
6. **LLM 预算耗尽返回 HTTP 429**（不再是 500）。额度耗尽时不重试，也不切换备用模型，并在当前进程内标记到下一个 UTC 0 点。
7. **Ask 流式接口新增 `error` 事件。** 模型中途失败时，服务端先发出 `error`（`code` 可能是 `unavailable`、`budget_exhausted`、`quota`），再发出回退的抽取式回答，最后是 `done`。客户端收到 `error` 时必须丢弃此前的 `delta` 和 `sentence`（`tests/test_ask.py::test_ask_error_mid_stream_emits_error_then_a_persisted_fallback`）。
8. **关系判定更保守。** 主体、指标或限定语不一致的 pair 不再被启发式判为 `supports` 或 `duplicate`，改交模型判定（DOM-24，`tests/test_relate_gate.py::test_subject_mismatch_defers_to_llm`）。**迁移不会重算已有的关系**：0009–0011 只改 `wiki_page_revisions`、`users`、`sources`，并新增 `rate_limits`、`share_links`，不触碰 `claim_relations`。若需要按新规则重算，请重新摄取相关来源（重算方式未在 v2.2 中提供专门命令，这一点需要人工确认）。
9. **上传的 Markdown 标题** 取第一个标题，无标题时取文件名（`tests/test_sources_api.py::test_uploaded_markdown_is_titled_by_its_first_heading`）。
10. **Wiki 修订摘要** 改为节级计数，如 `N updated, N added, N removed sections`。
11. **时间线参数优先级。** `start`/`end` 存在时忽略 `from`/`to`（`tests/test_filters_k54.py::test_timeline_start_end_win_over_from_to`）。
12. **日志与访问记录。** 每个请求写一条 access 日志，使用路由模板，用户标识为哈希后的值，不记录原始 id 与原始路径（`tests/test_observability.py::test_access_log_is_one_json_line_with_template_status_duration_and_hashed_user`）。`LOG_FORMAT=json` 时为一行 JSON；默认的 `text` 格式适合本地开发，容器与 compose 中设为 `json`。
13. **健康检查路径** 为根路径 `/healthz`、`/readyz`，不在 `/v1` 下。`/metrics` 同样在根路径。

---

## 4. 已知限制

以下限制本版本未解决：

1. **周计划无法改回每天。** `PATCH /v1/schedules/{id}` 忽略值为 `null` 的字段，因此周计划不能通过修改改回每天。界面禁用了该选项并提示删除后重建；重建会丢失该计划的运行历史，因为运行按 schedule id 关联。
2. **登录限流按邮箱计数。** 任何人都可以让某个已知邮箱在一个窗口内被暂时锁定。规格要求如此，尚未增加按 IP 的第二个维度。
3. **Wiki 阵营图不处理跨簇冲突。** `app/wiki/compiler.py::_camp_map` 只使用页面自身的主张和簇内关系，跨簇的冲突在 wiki 中不会标注两侧。API 和 MCP 的冲突视图已经修复。
4. **长事务未拆分（已推迟）。** 嵌套连接死锁已修复（独立的账本连接池），但 verify 与 ask 的请求级数据库会话仍在 LLM 调用期间保持占用。负载测试见 [LOADTEST.md](LOADTEST.md)。
5. **指标中的队列与 LLM 数值统计全部用户**，而不是调用方可见的范围。
6. **上传内容检查只在存储路径生效。** `captures/parse` 预览不存储，因此不做文件头校验。
7. **真实 LLM 质量目标未全部达成。** claims 的 dev 集为 0.7899（目标 0.80），test 集为 0.6732（目标 0.72）；线上 `wiki_compile` 一次未得到可解析答案。
8. **部署未在服务器上验证。** DEPLOY 中的命令已在本机执行，但 `docker compose up -d` 与开发栈端口冲突，需要在目标服务器上执行。
9. **计时测试在负载下不稳定。** `tests/test_scale_smoke.py` 的 5000 条插入预算为 5 秒，在内存压力下观察到 5.71–6.22 秒。这是环境原因，预算未被放宽。
10. **JWT 仍存于 localStorage。** 缓解措施为 24 小时有效期、`logout-all` 与 CSP；迁移到 HttpOnly cookie（需配合 CSRF 防护）推迟到后续版本。
11. **CI 没有依赖审计步骤**（例如 `pip-audit`、`npm audit`），需在接入 CI 时补上。

---

## 5. 发布前检查清单

- [ ] `make backup` 成功，备份已存放到异地。
- [ ] `.env` 已补充第 2 节的变量；`POSTGRES_PASSWORD`、`SOURCEHERO_SECRET_KEY` 已替换为真实值。
- [ ] `make migrate` 成功；`tests/test_migration_roundtrip.py` 通过。
- [ ] 生产环境：`ALLOW_REGISTRATION` 与 `DOCS_ENABLED` 按需显式设置。
- [ ] `METRICS_TOKEN` 已设置，监控系统使用该令牌抓取 `/metrics`。
- [ ] 如使用 OpenRouter：确认 `LLM_ALLOW_PAID` 未设置，`LLM_DAILY_REQUEST_BUDGET` 符合额度。
- [ ] 客户端已适配 Ask 的 `error` 事件，并通知用户升级后需重新登录。
