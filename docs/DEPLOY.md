# Deployment guide

This guide deploys SourceHero on a single machine with Docker Compose and Caddy. Every command says where it has been run:

- **Run on this machine**: `docker compose ... config -q`, `docker compose --profile app build`, `make backup`, `make restore`, `make loadtest`, `make dev`, `make demo`.
- **Not run on this machine**: `docker compose ... up -d`. It takes the same ports as the development stack, so it belongs on a real server.

## 1. Prepare

1. Install Docker and Docker Compose v2 (Compose 2.24 or newer, for the `required: false` form of `env_file`).
2. Copy the configuration: `cp .env.example .env`. Do **not** keep the development password (`sourcehero_dev`) in production.
3. Generate a secret and write it to `SOURCEHERO_SECRET_KEY` in `.env` (at least 32 bytes; the production guard refuses the default):

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

4. Set the database password `POSTGRES_PASSWORD`. Compose requires it, and it must match the password in `DATABASE_URL`.
5. Set the production switches: `SOURCEHERO_ENV=production`, `PUBLIC_WEB_URL=https://<your domain>`, `SITE_ADDRESS=<your domain>`. When `ALLOW_REGISTRATION` is unset, production defaults to `false`; when `DOCS_ENABLED` is unset, production serves neither `/docs` nor `/openapi.json`.

Validate the configuration (run, exit code 0):

```bash
POSTGRES_PASSWORD=<your password> docker compose --profile app --profile prod config -q
```

## 2. Build and start

Build the images (run, exit code 0):

```bash
POSTGRES_PASSWORD=<your password> docker compose --profile app build
```

`NEXT_PUBLIC_API_URL` is a build argument of the web image. Compose cannot give it a different value per profile, so in production export it before the build and let the frontend use the same-origin `/api` behind Caddy:

```bash
export NEXT_PUBLIC_API_URL=/api
```

Start (not run on this machine):

```bash
docker compose --profile app --profile prod up -d
```

Caddy in the `prod` profile listens on 80 and 443. `deploy/Caddyfile` forwards `/api/*` to the API and everything else to the web app. Caddy requests a TLS certificate automatically when `SITE_ADDRESS` is a public domain and ports 80 and 443 are reachable from the internet.

## 3. Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `SOURCEHERO_ENV` | yes | `production` enables the production guard |
| `SOURCEHERO_SECRET_KEY` | required in production | key for JWTs and encryption, at least 32 bytes, never the default |
| `POSTGRES_PASSWORD` | yes (compose) | password of the database container, must match `DATABASE_URL` |
| `DATABASE_URL` | yes | in production the password must not be `sourcehero_dev` |
| `PUBLIC_WEB_URL` | required in production | public URL; HSTS is sent only when it starts with `https://` |
| `SITE_ADDRESS` | required with Caddy | domain used for the certificate |
| `ALLOW_REGISTRATION` | no | `false` by default in production; only an explicit setting turns it on |
| `DOCS_ENABLED` | no | `/docs` is off by default in production; `true` opens it |
| `JWT_TTL_HOURS` | no | token lifetime, default 24 |
| `LOG_FORMAT` | no | `text` or `json`; the container images default to `json` |
| `METRICS_TOKEN` | no | empty makes `/metrics` return 404; when set it needs a Bearer token |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | no | database connection pool size, default 5 and 10 |
| `RATE_LIMIT_ENABLED` | no | off by default; when on, the thresholds below apply |
| `RATE_LIMIT_WINDOW_SECONDS` and `RATE_LIMIT_LOGIN` / `ASK` / `VERIFY` / `CAPTURE` / `UPLOAD` | no | fixed-window length and requests per window |
| `LLM_PROVIDER`, `LLM_PRESET`, `LLM_API_KEY` (or `LLM_AUTH`, `LLM_TOKEN_COMMAND`, OAuth variables) | no | see section 8 |
| `LLM_DAILY_TOKEN_BUDGET`, `LLM_DAILY_REQUEST_BUDGET` | no | daily budgets, 0 means unlimited |
| `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` | no | see section 9 |
| `FETCH_ALLOW_PRIVATE` | no | `false` by default; fetches refuse private addresses (SSRF guard) |

`.env.example` is the full list.

## 4. Create the first administrator

Registration is off by default in production. The first account to register becomes an administrator automatically (see `app/api/routers/auth.py`), so:

1. Set `ALLOW_REGISTRATION=true` temporarily and restart the API;
2. Register the first account through the web app; it becomes the administrator;
3. Set `ALLOW_REGISTRATION` back to `false` and restart the API.

`app.demo.seed` creates a regular user, not an administrator.

## 5. Backup and restore

Backup (run, `make backup`):

```bash
make backup
```

It dumps the database with `pg_dump -Fc` and tars the local storage directory into `backups/<UTC timestamp>/`, together with `MANIFEST.txt` (sha256 sums, no passwords). `backups/` is in `.gitignore`. The database connection comes from `.env`; when the host has no `pg_dump`, the script falls back to `pg_dump` inside the running Postgres container.

Restore (run, `make restore`, into a new database):

```bash
make restore ARGS="backups/<timestamp> sourcehero_restore_check"
```

The restore script refuses to overwrite the main database unless `--force` is given. `--force` also unpacks the tar back into the storage directory. The automated round trip lives in `tests/test_backup_restore.py` (marked `slow` and `db`) and compares row counts of the core tables and the migration version.

## 6. Upgrade and migrations

- The image entrypoint runs `alembic upgrade head` first when `RUN_MIGRATIONS=1`, then starts the process.
- On the host: `make migrate`.
- Current migration chain: `0009_revision_method` (how a revision was compiled) → `0010_security` (`token_version` on users and the rate-limit table) → `0011_features` (source reliability and read-only share links).

## 7. Observability

- **Logs**: with `LOG_FORMAT=json` every line is one JSON object carrying `request_id`. Each request writes one access line: method, route template (not the raw path), status, duration in milliseconds, and the first 12 hex characters of the sha256 of the user id. Callers may pass a request id with `X-Request-ID`.
- **Metrics**: `GET /metrics`. With an empty `METRICS_TOKEN` it returns 404; when set it needs `Authorization: Bearer <METRICS_TOKEN>` and a wrong token returns 401.
- **Do not scrape metrics through Caddy**: Caddy forwards only `/api/*` and web requests, not `/metrics`. Prometheus should reach the API container directly at `api:8000/metrics` on the same internal network.
- **Health**: `/healthz` (liveness) and `/readyz` (readiness). The worker's container health check runs `python -m app.jobs.worker --healthcheck`: it exits 0 only when the database is reachable and the heartbeat file `DATA_DIR/worker.heartbeat` was updated within the last 120 seconds.

## 8. LLM configuration

- The `openrouter` preset allows free models only (model ids ending in `:free`, or `openrouter/free`). The settings page states the limit.
- `LLM_ALLOW_PAID` defaults to `false`.
- The daily token and request budgets come from `LLM_DAILY_TOKEN_BUDGET` and `LLM_DAILY_REQUEST_BUDGET`; the settings page shows today's usage and the caps.
- A token command may set its own TTL (stored with the credential, 0–86400 seconds); unset falls back to `LLM_TOKEN_TTL_SECONDS`.

## 9. Switching the embedder and reindexing

- `EMBEDDING_PROVIDER` accepts `hashing` (default, offline), `fastembed` (local model) and `openai_compat`.
- After a switch, existing documents need their vectors rebuilt: sign in and call `POST /v1/index/reindex` (queued as a background job).

## 10. Load

Results, machine details and commands are in [LOADTEST.md](LOADTEST.md).

## 11. Known limits

- A weekly schedule cannot be changed back to daily through PATCH; the interface disables the option and asks you to delete and recreate it (recreating loses the run history).
- The login rate limit counts per email, so anyone can lock a known email address for a while; that is the specified behaviour, and a per-IP dimension has not been added.
- The wiki's camp annotations do not cover cross-cluster conflicts.
