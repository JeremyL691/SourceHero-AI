# Load smoke results

Target: with concurrency 10, an error rate below 1% in every scenario.

## Conclusion

- **Target met**: at concurrency 10 the error rate of the four scenarios (search, verify, ask, captures) was 0.00% in two independent runs (runs 3 and 4, see the table below).
- At concurrency 25 the error rate of all four scenarios was also 0.00% in the final run (run 4), but latency rose clearly, especially for verify and ask. The machine's load average sat between 16 and 59, which is why these numbers move.
- The runs found and fixed three real problems (see "Problems found"), each with a regression test.

## Machine and environment

| Item | Value |
|---|---|
| CPU | Apple M3 Pro, 11 cores |
| Memory | 18 GiB |
| OS | Darwin 27.0.0 (macOS) |
| Database | PostgreSQL 16.15, `pgvector/pgvector:pg16` container, `/dev/shm` at 1 GB (`shm_size: 1gb` in `docker-compose.yml`) |
| App | `make dev` (API and worker in development mode; the API runs with `--reload`, so latency is a little higher than a production deployment) |
| LLM | `LLM_PROVIDER=fake`, no cost |
| Embeddings | `EMBEDDING_PROVIDER=hashing` |
| Load | Other containers (Kafka, two more Postgres instances) ran at the same time; `uptime` load average between 16 and 59 |

## Commands

Seed data and a PAT:

```bash
make migrate
make dev
make demo
```

Pass the temporary PAT through the environment at run time (not on the command line, so `make` does not echo it):

```bash
LOADTEST_TOKEN="$TOKEN" make loadtest ARGS="--base-url http://127.0.0.1:<API_PORT> --concurrency 10 --duration 60 --scenarios search,verify,ask,captures --json-out .dev/logs/loadtest-c10.json"
LOADTEST_TOKEN="$TOKEN" make loadtest ARGS="--base-url http://127.0.0.1:<API_PORT> --concurrency 25 --duration 60 --scenarios search,verify,ask,captures --json-out .dev/logs/loadtest-c25.json"
```

The temporary PAT was revoked after the runs. For the runs, the development database's `LLM_DAILY_TOKEN_BUDGET` was raised to 500000000 (fake LLM, no cost) and restored afterwards.

## Results (run 4, final state)

Concurrency 10 (60 seconds per scenario):

| Scenario | Requests | Errors | Error rate | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|---|---|---|---|
| search | 1422 | 0 | 0.00% | 23.57 | 387.92 | 676.22 | 911.38 |
| verify | 205 | 0 | 0.00% | 3.22 | 2991.01 | 9204.10 | 9934.53 |
| ask | 524 | 0 | 0.00% | 8.65 | 1051.81 | 1774.44 | 2915.94 |
| captures | 10045 | 0 | 0.00% | 167.31 | 54.66 | 92.87 | 138.76 |

Concurrency 25 (60 seconds per scenario):

| Scenario | Requests | Errors | Error rate | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|---|---|---|---|
| search | 1214 | 0 | 0.00% | 19.91 | 1160.64 | 1842.15 | 3396.44 |
| verify | 252 | 0 | 0.00% | 3.80 | 2142.60 | 17161.27 | 22470.14 |
| ask | 582 | 0 | 0.00% | 9.44 | 2301.48 | 4681.73 | 6981.87 |
| captures | 9997 | 0 | 0.00% | 166.35 | 140.27 | 225.52 | 303.80 |

## Run history and problems found

The JSON output of each run is in `.dev/logs/` (not version controlled).

1. **Run 1**: at concurrency 10, `ask` had a 100% error rate. The API log showed `app.llm.types.BudgetExceededError`; its HTTP mapping was missing, so the result was 500 instead of 429. The development database's daily token budget had run out before the load.
   - Fix: `app/main.py` registers the same 429 handler for LLM budget errors as for the core error. Regression test: `tests/test_ask.py::test_ask_over_the_llm_budget_is_429_with_the_budget_code`.
2. **Run 2**: at concurrency 25, `verify` and `ask` all hit the 30 second timeout. The log said `QueuePool limit of size 5 overflow 10 reached`. The root cause was a nested connection checkout: the LLM ledger writes (cache, run record, budget reservation) asked the same pool for a second connection while the request already held one; when the pool was exhausted, every request waited for that second connection.
   - Fix: the LLM ledger writes use their own ledger pool (`app/core/db.py::LedgerSessionLocal`). Ledger transactions still commit on their own, so a rolled-back business transaction does not delete the run record or the budget reservation. Regression tests: `tests/test_llm.py::test_llm_bookkeeping_does_not_draw_from_the_request_pool` and the existing `test_business_transaction_rollback_does_not_affect_llm_calls`.
3. **Run 3**: at concurrency 25, `search` had a 70% error rate, `verify` 44% and `ask` 15%. The errors were `could not resize shared memory segment ... No space left on device`. The database container's `/dev/shm` was Docker's default 64 MB.
   - Fix: the `db` service in `docker-compose.yml` sets `shm_size: 1gb`. The development database container was recreated (the volume was kept); `/dev/shm` is now 1 GB.
4. **Run 4**: after the three fixes above, every scenario reported a 0.00% error rate at concurrency 10 and 25.

## Limits

- The results come from a development machine that ran other containers at the same time, with a high load average. The latency numbers only compare runs inside the same window; they are not a production capacity statement.
- Between run 2 and run 4 the `search` p50 rose from about 107 ms to about 388 ms (concurrency 10). The machine's load rose over that window, and the API gained a request-context middleware and a security-headers middleware, both of which do very little work. Their cost has not been measured separately.
- The development API runs with `--reload`; a production deployment should run a process without it.
- A request session still spans the LLM call. The long-transaction work fixed only the nested-connection deadlock; the rest is deferred (see the known limits in [RELEASE_NOTES_v2.2.0rc1.md](RELEASE_NOTES_v2.2.0rc1.md)).
