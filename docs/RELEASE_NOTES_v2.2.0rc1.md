# SourceHero v2.2.0rc1 release notes (operations)

> For deployment and operations. The change overview is in [CHANGELOG.md](../CHANGELOG.md); deployment steps are in [docs/DEPLOY.md](DEPLOY.md).

---

## 1. Upgrade steps

1. **Back up first.** `make backup` (`scripts/backup.sh`). Confirm the dump exists and is not empty. Restore with `make restore ARGS=...` (`scripts/restore.sh` refuses to overwrite the main database; only `--force` unpacks the storage directory). Round trip: `tests/test_backup_restore.py::test_backup_then_restore_matches_source`.
2. **Update the code and `.env`.** Add the variables listed in section 2 against `.env.example`.
3. **Run the migrations:** `make migrate` (that is `alembic upgrade head`). v2.2 adds three migrations, all of which only add columns or tables and change no existing data:

   | Migration | Change |
   |---|---|
   | `0009_revision_method` | nullable string column `wiki_page_revisions.compile_method`. Revisions from before the migration have it empty and the interface does not show it |
   | `0010_security` | integer column `users.token_version` (default 0); new table `rate_limits` (primary key `key, window_start`) |
   | `0011_features` | `sources.reliability` (default `unknown`, CHECK limited to unknown/low/medium/high); `sources.reliability_note`; new table `share_links` (stores only the sha256 of the token) |

   Each step downgrades on its own; `tests/test_migration_roundtrip.py::test_each_step_upgrades_and_downgrades_its_own_schema` checks them step by step.
4. **Production deployment.** `POSTGRES_PASSWORD` is required in `docker-compose.yml` (`${POSTGRES_PASSWORD:?...}`); without it compose exits with an error (OPS-9). The web image reads `NEXT_PUBLIC_API_URL` at build time; a same-origin deployment sets it to `/api`, otherwise the image must be rebuilt.
5. **Sign in again after the upgrade.** See the JWT note in section 3.

---

## 2. New environment variables

These variables are new relative to the v2.1.0-rc1 `.env.example` (`git diff v2.1.0-rc1 -- .env.example` shows the same list). Defaults come from `app/core/config.py`.

| Variable | Default | Meaning |
|---|---|---|
| `LOG_FORMAT` | `text` | `json` writes structured logs; containers and compose set it to `json` |
| `METRICS_TOKEN` | empty | bearer token for `/metrics`; empty makes `/metrics` return 404 (`tests/test_metrics.py`) |
| `DOCS_ENABLED` | `true` in development; `false` in production | whether `/docs` and `/openapi.json` are served; in production only an explicit setting opens them (SEC-18) |
| `DB_POOL_SIZE` | `5` | SQLAlchemy pool size per process |
| `DB_MAX_OVERFLOW` | `10` | extra connections allowed above the pool |
| `RATE_LIMIT_ENABLED` | `false` | enables the Postgres fixed-window rate limits (SEC-20) |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | window length in seconds |
| `RATE_LIMIT_LOGIN` | `10` | logins per window (counted per email) |
| `RATE_LIMIT_ASK` | `30` | ask calls per window (per user) |
| `RATE_LIMIT_VERIFY` | `30` | verify calls per window; public share reads reuse this value (per client host) |
| `RATE_LIMIT_CAPTURE` | `60` | captures per window |
| `RATE_LIMIT_UPLOAD` | `20` | uploads per window |
| `SHARING_ENABLED` | `true` | `false` makes every share route (including public reads) return 404 |
| `POSTGRES_PASSWORD` | none (required) | password of the database container in compose, must match `DATABASE_URL` |
| `LLM_TOKEN_TTL_SECONDS` | `300` | default TTL when a command credential does not set its own |
| `LLM_DAILY_REQUEST_BUDGET` | `0` (unlimited); the openrouter preset defaults to `800` | instance-wide request cap per UTC day; above it the app falls back without calling the model |
| `LLM_CASSETTE_MODE` | `off` | `record` or `replay`, for recording and offline replay of real model results |
| `LLM_CASSETTE_PATH` | empty | path of the cassette file |
| `LLM_EXTRA_BODY` | empty | JSON object merged into every request body |
| `LLM_ALLOW_PAID` | `false` | only `true` allows paid models outside `:free`. This project forbids setting it |
| `OPENROUTER_API_KEY` | empty | OpenRouter key; lives only in the local `.env` and must never be committed (`tests/test_no_secrets.py`) |
| `RERANK_PROVIDER` | `none` | `fastembed` enables the reranker |
| `RERANK_MODEL` | `Xenova/ms-marco-MiniLM-L-6-v2` | reranker model name |
| `RETRIEVAL_CANDIDATES` | `50` | candidates per retrieval arm before reranking |
| `RRF_K` | `60` | constant k of the reciprocal rank fusion |

Also, the default of `JWT_TTL_HOURS` changed from 168 to 24 (section 3); check the same entry in `.env.example`.

---

## 3. Behaviour changes

v2.2 removes and renames no v2.1 API path (against v2.1.0-rc1's `dashboard-web/openapi.json`: all 66 paths remain, now 85). The changes below do not show up as breaking changes in the API schema, but operations and clients should know them:

1. **JWT lifetime** changed from 168 hours to **24 hours** (`jwt_ttl_hours` in `app/core/config.py`; SEC-19).
2. **Old JWTs very likely stop working after the upgrade.** The new version requires a `token_version` claim in the JWT; `tests/test_auth.py::test_jwt_without_token_version_claim_is_rejected` shows that a JWT without it is rejected. Tokens issued before the upgrade should therefore require a new sign-in. This is inferred from the test; confirm it in staging. Personal access tokens (PATs) are unaffected.
3. **Registration is off by default in production.** `ALLOW_REGISTRATION` is `false` in production unless set explicitly (`tests/test_production_guard.py::test_registration_defaults_to_off_in_production`). The previous behaviour, where the first registrant became an administrator, no longer applies in production.
4. **Production refuses weak configuration at startup.** The app refuses to start when the secret is shorter than 32 bytes (counted as UTF-8 bytes) or the database still uses the development password (`tests/test_production_guard.py::test_production_refuses_a_secret_shorter_than_32_bytes`, `::test_production_refuses_the_development_database_password`).
5. **The login rate limit** now counts in Postgres and **counts every attempt**, not only failures. It counts per email, is off by default, and needs `RATE_LIMIT_ENABLED=true`.
6. **An exhausted LLM budget returns HTTP 429** (no longer 500). Quota exhaustion is not retried and does not switch to a fallback model; the process remembers it until the next UTC midnight.
7. **The Ask stream gained an `error` event.** When the model fails mid-stream the server first sends `error` (`code` may be `unavailable`, `budget_exhausted` or `quota`), then a fallback extractive answer, and finally `done`. A client that receives `error` must discard the `delta` and `sentence` events before it (`tests/test_ask.py::test_ask_error_mid_stream_emits_error_then_a_persisted_fallback`).
8. **Relation judging is more conservative.** A pair whose subject, metric or qualifier disagrees is no longer labelled `supports` or `duplicate` by the heuristic; it goes to the model (DOM-24, `tests/test_relate_gate.py::test_subject_mismatch_defers_to_llm`). **The migration does not recompute existing relations**: 0009–0011 only touch `wiki_page_revisions`, `users` and `sources` and add `rate_limits` and `share_links`; they leave `claim_relations` alone. To apply the new rules, re-ingest the sources (v2.2 ships no dedicated recompute command; this needs a human decision).
9. **Uploaded Markdown is titled by its first heading**, falling back to the file name (`tests/test_sources_api.py::test_uploaded_markdown_is_titled_by_its_first_heading`).
10. **Wiki revision summaries** are now per-section counts, such as `N updated, N added, N removed sections`.
11. **Timeline parameter precedence.** `start`/`end` win over `from`/`to` when both are given (`tests/test_conflicts_timeline_filters.py::test_timeline_start_end_win_over_from_to`).
12. **Logs and access records.** Every request writes one access line using the route template, with the user identified by a hash; raw ids and raw paths are never logged (`tests/test_observability.py::test_access_log_is_one_json_line_with_template_status_duration_and_hashed_user`). With `LOG_FORMAT=json` it is one JSON line; the default `text` format suits local development, while containers and compose use `json`.
13. **Health endpoints** are at the root: `/healthz` and `/readyz`, not under `/v1`. `/metrics` is at the root as well.

---

## 4. Known limits

These limits remain in this version:

1. **A weekly schedule cannot go back to daily.** `PATCH /v1/schedules/{id}` ignores fields whose value is `null`, so a weekly schedule cannot be changed back to daily. The interface disables the option and asks you to delete and recreate it; recreating loses the schedule's run history, because runs are linked by schedule id.
2. **The login rate limit counts per email.** Anyone can temporarily lock a known email address for one window. That is what the specification asks for, and a second dimension per IP has not been added.
3. **The wiki camp map does not cover cross-cluster conflicts.** `app/wiki/compiler.py::_camp_map` uses only the page's own claims and within-cluster relations, so cross-cluster conflicts are not marked on both sides in the wiki. The API and MCP conflict views are fixed.
4. **Long transactions are not split (deferred).** The nested-connection deadlock is fixed (a separate ledger pool), but the request-level database session of verify and ask is still held during the LLM call. The load test is in [LOADTEST.md](LOADTEST.md).
5. **The queue and LLM numbers in the metrics count all users**, not the caller's visible scope.
6. **The upload content check only runs on the storage path.** `captures/parse` previews store nothing, so they skip the magic-bytes check.
7. **The real-LLM quality targets are not all met.** claims reaches 0.7899 on the dev set (target 0.80) and 0.6732 on the test set (target 0.72); live `wiki_compile` once returned no parseable answer.
8. **The deployment is not verified on a server.** The commands in DEPLOY were run on this machine, but `docker compose up -d` clashes with the development stack's ports and has to run on the target server.
9. **The timing test is unstable under load.** `tests/test_scale_smoke.py` budgets 5 seconds for 5000 inserts and was observed at 5.71–6.22 seconds under memory pressure. That is an environment effect; the budget was not relaxed.
10. **The JWT still lives in localStorage.** The mitigations are the 24 hour lifetime, `logout-all` and CSP; moving to an HttpOnly cookie (with CSRF protection) is deferred to a later version.
11. **CI has no dependency audit step** (such as `pip-audit` or `npm audit`); add one when CI is set up.

---

## 5. Pre-release checklist

- [ ] `make backup` succeeded and the backup is stored off-site.
- [ ] `.env` has the variables from section 2; `POSTGRES_PASSWORD` and `SOURCEHERO_SECRET_KEY` hold real values.
- [ ] `make migrate` succeeded; `tests/test_migration_roundtrip.py` passes.
- [ ] Production: `ALLOW_REGISTRATION` and `DOCS_ENABLED` are set explicitly as needed.
- [ ] `METRICS_TOKEN` is set and the monitoring system scrapes `/metrics` with it.
- [ ] With OpenRouter: confirm `LLM_ALLOW_PAID` is unset and `LLM_DAILY_REQUEST_BUDGET` fits the quota.
- [ ] Clients handle the Ask `error` event, and users are told they must sign in again after the upgrade.
