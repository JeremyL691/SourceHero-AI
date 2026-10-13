# 负载冒烟结果

目标：并发 10 时，所有场景的错误率 < 1%。

## 结论

- **目标达成**：并发 10 时，search、verify、ask、captures 四个场景的错误率在两次独立运行中都是 0.00%（运行 3 与运行 4，见下表）。
- 并发 25 在最终运行（运行 4）中四个场景的错误率也都是 0.00%，但延迟明显升高，尤其是 verify 与 ask。本机的负载平均值在 16–59 之间，是这些数字波动较大的原因。
- 测试过程中发现并修复了三个真实问题（见"发现的问题"），每一项都有回归测试。

## 机器与环境

| 项目 | 值 |
|---|---|
| CPU | Apple M3 Pro，11 核 |
| 内存 | 18 GiB |
| 系统 | Darwin 27.0.0（macOS） |
| 数据库 | PostgreSQL 16.15，`pgvector/pgvector:pg16` 容器，`/dev/shm` 为 1 GB（`docker-compose.yml` 中的 `shm_size: 1gb`） |
| 应用 | `make dev`（API 与 worker 为开发模式，API 带 `--reload`，因此延迟略高于生产部署） |
| LLM | `LLM_PROVIDER=fake`，不产生费用 |
| 嵌入 | `EMBEDDING_PROVIDER=hashing` |
| 负载 | 其他容器（如 Kafka、另外两个 Postgres）同时运行，`uptime` 的负载平均值在 16–59 之间 |

## 命令

种子数据与 PAT：

```bash
make migrate
make dev
make demo
```

运行时通过环境变量传入临时的 PAT（不写入命令行，避免 `make` 回显）：

```bash
LOADTEST_TOKEN="$TOKEN" make loadtest ARGS="--base-url http://127.0.0.1:<API_PORT> --concurrency 10 --duration 60 --scenarios search,verify,ask,captures --json-out .dev/logs/loadtest-c10.json"
LOADTEST_TOKEN="$TOKEN" make loadtest ARGS="--base-url http://127.0.0.1:<API_PORT> --concurrency 25 --duration 60 --scenarios search,verify,ask,captures --json-out .dev/logs/loadtest-c25.json"
```

运行结束后撤销临时 PAT。运行期间为开发库临时把 `LLM_DAILY_TOKEN_BUDGET` 调高到 500000000（假 LLM，没有费用），运行结束后恢复为原值。

## 结果（运行 4，最终状态）

并发 10（每个场景 60 秒）：

| 场景 | 请求数 | 错误 | 错误率 | 吞吐（请求/秒） | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|---|---|---|---|
| search | 1422 | 0 | 0.00% | 23.57 | 387.92 | 676.22 | 911.38 |
| verify | 205 | 0 | 0.00% | 3.22 | 2991.01 | 9204.10 | 9934.53 |
| ask | 524 | 0 | 0.00% | 8.65 | 1051.81 | 1774.44 | 2915.94 |
| captures | 10045 | 0 | 0.00% | 167.31 | 54.66 | 92.87 | 138.76 |

并发 25（每个场景 60 秒）：

| 场景 | 请求数 | 错误 | 错误率 | 吞吐（请求/秒） | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|---|---|---|---|
| search | 1214 | 0 | 0.00% | 19.91 | 1160.64 | 1842.15 | 3396.44 |
| verify | 252 | 0 | 0.00% | 3.80 | 2142.60 | 17161.27 | 22470.14 |
| ask | 582 | 0 | 0.00% | 9.44 | 2301.48 | 4681.73 | 6981.87 |
| captures | 9997 | 0 | 0.00% | 166.35 | 140.27 | 225.52 | 303.80 |

## 运行历史与发现的问题

每次运行的 JSON 输出在 `.dev/logs/`（不纳入版本控制）。

1. **运行 1**：并发 10 时 `ask` 错误率 100%。API 日志显示 `app.llm.types.BudgetExceededError`，它的 HTTP 映射缺失，结果是 500 而不是 429。开发库的每日 token 预算在负载前已经用尽。
   - 修复：`app/main.py` 为 LLM 的预算错误注册了与核心错误相同的 429 处理器。回归测试：`tests/test_ask.py::test_ask_over_the_llm_budget_is_429_with_the_budget_code`。
2. **运行 2**：并发 25 时 `verify` 与 `ask` 全部在 30 秒超时。日志为 `QueuePool limit of size 5 overflow 10 reached`。根因是嵌套的连接获取：LLM 调用的账本写入（缓存、运行记录、预算预留）在请求已经持有一个连接的同时，又从同一连接池申请第二个连接；池耗尽时，所有请求都在等第二个连接。
   - 修复：LLM 账本写入改用独立的账本连接池（`app/core/db.py::LedgerSessionLocal`）。账本事务仍然独立提交，所以业务事务回滚不会删除运行记录或预算预留。回归测试：`tests/test_llm.py::test_llm_bookkeeping_does_not_draw_from_the_request_pool`，以及既有的 `test_business_transaction_rollback_does_not_affect_llm_calls`。
3. **运行 3**：并发 25 时 `search` 错误率 70%，`verify` 44%，`ask` 15%。错误为 `could not resize shared memory segment ... No space left on device`。数据库容器的 `/dev/shm` 是 Docker 默认的 64 MB。
   - 修复：`docker-compose.yml` 的 `db` 服务设置 `shm_size: 1gb`。已重建开发数据库容器（卷保留），`/dev/shm` 现为 1 GB。
4. **运行 4**：上述三项修复后，并发 10 与 25 下所有场景的错误率均为 0.00%。

## 局限

- 结果来自一台开发机，同时运行着其他容器，负载平均值高。延迟数字只能用于比较同一时段内的运行，不能当作生产容量。
- 运行 2 与运行 4 之间 `search` 的 p50 从约 107 ms 上升到约 388 ms（并发 10）。这段时间本机负载上升，同时 API 增加了请求上下文中间件与安全响应头中间件，两者都只做很少的工作。尚未单独量化它们的开销。
- 开发模式的 API 带 `--reload`，生产部署应使用不带 reload 的进程。
- 请求会话仍然横跨 LLM 调用。长事务问题只修复了嵌套连接的死锁部分，其余部分已推迟（见 [RELEASE_NOTES_v2.2.0rc1.md](RELEASE_NOTES_v2.2.0rc1.md) 的已知限制）。
