# Changelog

## v0.7.0

Cloud version. Swapped SQLite for Postgres, added auth, built a real frontend.

- Moved to PostgreSQL + pgvector for data and embeddings
- Added Supabase Auth for user accounts
- Replaced Streamlit with a Next.js frontend
- Cloudflare R2 for file uploads
- All data is now per-user (multi-tenant)
- Docker Compose for local dev

## v0.6.0

Quick capture feature — paste a URL or note from your clipboard.

- New capture APIs for clipboard text and URLs
- Quick Capture window in the Electron shell
- Clip sources for standalone notes and excerpts

## v0.5.0

Hybrid search and scheduled jobs.

- Semantic search via OpenAI embeddings + local vector index
- Recurring ingestion and briefing schedules
- Better settings UI for API key and model selection

## v0.4.0

Renamed to SourceHero. Cross-platform desktop builds.

- Works on macOS and Windows from the same codebase
- In-app API key configuration
- First-run demo seeding
- Friendly error messages

## v0.3.0

Added collections, tags, and PDF support.

## v0.2.0

Basic ingestion and search working.

## v0.1.0

First working version.
