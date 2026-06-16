# SourceHero

A research companion that turns your saved webpages, PDFs, and RSS feeds into a searchable knowledge base with cited answers.

Built with FastAPI, PostgreSQL, pgvector, and Next.js.

## Why I built this

I kept running into the same problem: I'd save articles, papers, and links faster than I could organize them. Bookmarks piled up, PDFs sat in Downloads, and RSS feeds went unread. When I actually needed something, I'd spend more time digging through folders than the original research would have taken.

I wanted something narrower than a general AI chatbot — a tool that works only with sources I explicitly trust, and always shows where its answers come from.

## What it does

- **Capture** — Add webpages, RSS feeds, PDFs, or quick notes
- **Index** — Chunks and deduplicates content, builds a search index
- **Ask** — Search across your library with lexical + semantic retrieval, get answers with citations
- **Briefing** — Generate evidence-grounded summaries on any topic
- **Schedule** — Set up recurring ingestion and briefing jobs

The app works without an OpenAI key (falls back to lexical search and extractive answers), but OpenAI improves synthesis and semantic search when configured.

## Architecture

```
User → Next.js (Vercel) → FastAPI (Railway) → PostgreSQL + pgvector
                                           → Cloudflare R2 (file storage)
```

I went with PostgreSQL + pgvector instead of a dedicated vector database to keep the stack simple — one database handles both structured data and embeddings. Cloudflare R2 stores uploaded files (S3-compatible, no egress fees).

## Tech choices

| What | Why |
|------|-----|
| FastAPI | Fast to build, good type safety, async support |
| PostgreSQL + pgvector | One DB for everything, avoids vector DB sprawl |
| Next.js | SSR where needed, good DX, deploys easily to Vercel |
| Cloudflare R2 | S3-compatible, no egress fees, cheap |
| Supabase Auth | JWT verification without building my own auth |

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

## Cloud deployment

The app expects three managed services plus an auth provider. Pick any combo that fits.

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

1. Postgres + pgvector reachable → backend starts (`uvicorn app.main:app`)
2. Backend runs `init_db()` (creates missing tables) and starts the scheduler poller
3. Frontend hits `/health` to confirm the API is up

The backend uses `Base.metadata.create_all()` on startup, so schema migrations are
additive only. For destructive changes, edit `infra/supabase/schema.sql` and
apply manually — Alembic is listed in dev dependencies but not yet wired up.

### Setting `OPENAI_MODEL=gpt-5.4-mini`

The default model is `gpt-5.4-mini`. Override via the `OPENAI_MODEL` env var or
in the Settings UI once you're logged in.

## What I'd do differently

- **Embeddings storage** — pgvector works fine for small-to-medium libraries, but I'd probably reach for a dedicated vector DB (Qdrant, Weaviate) if this needed to scale past ~100k chunks.
- **Background jobs** — The scheduler runs in-process right now. For production I'd pull in Celery or a proper job queue.
- **Frontend state** — I'm fetching data on every page load. A proper client-side cache (React Query / SWR) would make the UI feel snappier.
- **Tests** — The test suite covers the core pipeline but I'd want more integration tests, especially around the auth flow.

## License

MIT
