# SourceHero

**A self-hosted research workspace that turns saved webpages, RSS feeds, and PDFs into a searchable, citable knowledge base.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Next.js 16](https://img.shields.io/badge/Next.js-16-000000?logo=next.js&logoColor=white)](dashboard-web/package.json)
[![Tests: 47 collected](https://img.shields.io/badge/Tests-47%20collected%20%E2%80%A2%2013%20modules-blue)](tests/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](docker-compose.yml)

It is a FastAPI + PostgreSQL + Next.js app with Supabase Auth, Cloudflare R2 object storage, and Docker for local dev. An OpenAI key is optional: without it, search falls back to lexical retrieval and extractive answers; with it, you get synthesized briefings. The production schema defines a pgvector column and HNSW index for semantic search, but that path is not wired up yet. More on that in *What I'd do differently*.

---

## Preview

| Home | Library | Ask |
|---|---|---|
| ![Home](docs/assets/sourcehero-home.png) | ![Library](docs/assets/sourcehero-library.png) | ![Ask](docs/assets/sourcehero-ask.png) |

| Capture | Briefing |
|---|---|
| ![Capture](docs/assets/sourcehero-capture.png) | ![Briefing](docs/assets/sourcehero-briefing.png) |

---

## Why I built this

For months I saved articles, papers, and links faster than I could organize them. Bookmarks piled up, PDFs sat in Downloads, RSS feeds went unread. When I actually needed something back, I spent more time digging through folders than the original research would have taken.

I also wanted something narrower than a generic AI chatbot: a tool that works only with sources I explicitly trust, and always shows where its answers come from. Every claim is grounded in a chunk you can click through to.

---

## Design notes

- **Multi-format ingestion.** Webpages, RSS feeds, PDFs, and notes are captured and normalized into chunks through one pipeline in `app/` (ingestion routers and services).
- **Content-hash deduplication.** Every captured source is fingerprinted by content hash, so re-ingesting the same material never duplicates the library.
- **Retrieval.** Lexical TF-IDF is what runs today. The production schema already has an `embedding vector(1536)` column and HNSW index, but the write path and the vector query are not implemented yet. See *What I'd do differently*.
- **Storage layering.** Relational data (and future vectors) live in PostgreSQL; source files go to Cloudflare R2, which speaks the same S3 API I use locally with MinIO.
- **Multi-tenant scoping.** Every query is isolated per authenticated user via Supabase JWT claims, enforced at the service layer.
- **Scheduled jobs.** Ingestion and briefing runs are driven by a small in-process poller with a configurable interval; the local stack comes up with `docker compose up -d`.

## Key features

- **Capture** - add webpages, RSS feeds, PDFs, or quick notes from the clipboard.
- **Index** - chunks with overlap, deduplicated by content hash, indexed lexically. The pgvector index exists in the schema but is not powering search yet.
- **Ask** - search across your library; answers come back with clickable citations to the source chunk.
- **Briefing** - evidence-grounded summaries on any topic, with the same citation trail.
- **Schedule** - recurring ingestion and briefing jobs run in the in-process poller.
- **Multi-tenant** - every query is scoped to the authenticated user through Supabase JWT.

---

## Architecture

```mermaid
flowchart LR
    User([User]) -->|HTTPS| Next[Next.js 16<br/>dashboard-web]
    Next -->|JWT| API[FastAPI<br/>app/main.py]
    API -->|SQL| PG[(PostgreSQL 16<br/>pgvector schema)]
    API -->|S3 SDK| R2[(Cloudflare R2<br/>file storage)]
    API -->|optional| OAI[OpenAI API<br/>embeddings + synthesis]
    API -->|poll| Sched[In-process<br/>scheduler]
    Sched --> PG
```

The data flow is: `web / RSS / PDF -> extract + normalize -> content-hash dedup -> chunk -> lexical index -> tenant-scoped retrieval with citations`.

I picked PostgreSQL over a dedicated vector database to keep the stack simple: one database handles the structured data, and the pgvector column plus HNSW index are defined in the schema for the semantic-search milestone. Cloudflare R2 stores uploaded files because it is S3-compatible with no egress fees. Supabase Auth issues JWTs so the backend never manages credentials itself.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI | Async, Pydantic-typed contracts, automatic OpenAPI docs |
| Database | PostgreSQL 16 with pgvector schema (SQLite in tests) | One DB for relational data; vector column and HNSW index defined for semantic search |
| Auth | Supabase Auth (JWT) | No password storage, no session management; RS256 verification on the backend |
| Object storage | Cloudflare R2 | S3-compatible API, zero egress fees |
| Frontend | Next.js 16 + React 19 + TypeScript | App Router, RSC where useful, deploys to Vercel in one click |
| Styling | Tailwind v4 | Utility-first, no design-system overhead for a focused app |
| Container | Docker Compose | One command to bring up Postgres + MinIO for local dev |
| Tests | pytest | 47 collected across 13 modules (39 passing locally, 8 expected skips where Postgres/pgvector or file-index infra is needed) |
| LLM (optional) | OpenAI | Embeddings + answer synthesis. The app is fully functional without it. |

---

## Repository layout

```text
SourceHero-AI/
├── app/                    # FastAPI backend (routers, services, models, schemas)
├── dashboard-web/          # Next.js 16 frontend (App Router, Tailwind, Supabase client)
├── tests/                  # pytest suite (47 tests, 13 modules)
├── infra/supabase/         # Schema SQL (idempotent, run on cold start)
├── docs/assets/            # Screenshots used in this README
├── docker-compose.yml      # Local Postgres + MinIO
└── pyproject.toml          # Backend dependencies and tool config
```

---

## Running locally

```bash
git clone https://github.com/JeremyL691/SourceHero-AI.git
cd SourceHero-AI

# Start PostgreSQL + MinIO (local S3)
docker-compose up -d

# Backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env: set DATABASE_URL, SUPABASE_*, R2_*, OPENAI_API_KEY (optional)
uvicorn app.main:app --reload

# Frontend
cd dashboard-web
npm install
cp .env.example .env.local
npm run dev
```

Then open http://localhost:3000.

---

## Testing

```bash
source .venv/bin/activate
pytest -q
```

47 tests collected across 13 modules; 39 pass locally, 8 skip where Postgres/pgvector or file-index infra is needed. Coverage includes ingestion (web, RSS, PDF), chunking, deduplication, lexical retrieval and hybrid scaffolding, citations, schedules, and library scoping. Known gap: auth-flow integration tests are not covered yet (see *What I'd do differently*).

## Cloud deployment

The app expects three managed services plus an auth provider:

| Service | Suggested provider | Required env vars |
|---|---|---|
| Postgres (with pgvector) | Supabase, Neon, Railway | `DATABASE_URL` |
| Auth (JWT) | Supabase Auth | `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY` |
| Object storage (S3-compatible) | Cloudflare R2 | `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` |
| Frontend | Vercel | `NEXT_PUBLIC_API_URL` |
| Backend | Railway, Fly.io, Render | All of the above + `OPENAI_API_KEY` (optional) |

### One-time setup

```bash
# 1. Enable pgvector on your Postgres instance
psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 2. Apply the schema (idempotent)
psql "$DATABASE_URL" -f infra/supabase/schema.sql

# 3. Set environment variables on your backend host (see .env.example)
# 4. Configure CORS_ORIGINS to your frontend URL
```

### Run order on cold start

1. Postgres + pgvector reachable, then the backend starts (`uvicorn app.main:app`).
2. Backend runs `init_db()` (creates missing tables) and starts the scheduler poller.
3. Frontend hits `/health` to confirm the API is up.

The backend uses `Base.metadata.create_all()` on startup, so schema migrations are additive only. Destructive changes go through `infra/supabase/schema.sql` manually; Alembic is in dev dependencies but not wired up yet.

### Setting `OPENAI_MODEL`

The default model is `gpt-4o-mini`, configurable via the `OPENAI_MODEL` env var or in the Settings UI once you are logged in.

---

## What I'd do differently

- **Semantic search is schema-ready, not runtime-ready.** On rebuild I compute OpenAI `text-embedding-3-small` vectors and only record an `embedding_id`; the bytes are not written to the `vector(1536)` column and `_get_embedding_from_metadata()` returns `None`, so retrieval in `app/retrieval/search.py` stays lexical. Wiring the write and the `vector_cosine_ops` query is the next milestone.
- **Background jobs.** The scheduler is an in-process poller inside FastAPI. That works for personal use, but in real production I would move it to Celery or a dedicated worker so a deploy does not stall jobs.
- **Frontend state.** The UI fetches data on every page load. A proper client-side cache (React Query / SWR) would make it feel snappier.
- **Tests.** The suite covers the core pipeline (chunking, dedup, hybrid retrieval, citations, schedules, library). I want more integration tests, especially around the auth flow.
- **Migrations.** Alembic is installed but not wired up. Right now `Base.metadata.create_all()` handles additive schema; destructive changes need a manual SQL run.

---

## About me

Built by **Jeremy Liu** · UC Berkeley '27 · [GitHub @JeremyL691](https://github.com/JeremyL691)

If you are looking at this for a role, the parts I would point to first: end-to-end ownership of a multi-tenant backend (FastAPI + Postgres + R2), a working RAG pipeline with hybrid retrieval and click-through citations, and a Next.js frontend that talks to it through JWT-scoped APIs.

---

## License

[MIT](LICENSE)