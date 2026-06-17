# Changelog

> **SourceHero-AI** is a self-hosted research workspace that ingests webpages, RSS feeds, and PDFs, and lets you search and ask over them with cited answers.
>
> **Latest:** [v0.7.1](#v071) — cloud-native, PostgreSQL + pgvector, multi-tenant via Supabase Auth.
> **Earlier (v0.4 – v0.6):** local-first desktop era (Electron + SQLite + Streamlit).

## v0.7.1

_Post-cloud-migration polish._

- Fixed test suite (41 → 0 failures, 3 → 0 errors): 39 passing, 8 skipped
- Removed obsolete SQLite/Streamlit-era tests (`test_config_paths`, `test_config_readonly`, `test_dashboard`)
- Removed `test_settings_dropdown` (was testing a stub `user_settings` module)
- `app/models.py`: switched `user_id` columns from `postgresql.UUID` to `String(36)` so the same models run on SQLite (tests) and Postgres (production)
- `app/config.py`: added `SOURCEHERO_VECTOR_DIR` (with sensible default) and `SOURCEHERO_SCHEDULER_POLL_SECONDS` env vars
- `app/services/pipeline.py::_sanitize_error`: now recognises HTTP 403 / forbidden responses and matches DNS / timeout test expectations
- Added `auth_client` fixture in `tests/conftest.py` (FastAPI client + SQLite + fake User override)
- `.env.example`: documented new env vars, dropped stale `SOURCEHERO_DASHBOARD_PORT` (Streamlit is gone), added deployment note
- `docker-compose.yml`: added healthcheck for MinIO
- `README.md`: new "Cloud deployment" section with provider/env table

## v0.7.0

_Cloud version. Swapped SQLite for Postgres, added auth, built a real frontend._

- Moved to PostgreSQL + pgvector for data and embeddings
- Added Supabase Auth for user accounts
- Replaced Streamlit with a Next.js frontend
- Cloudflare R2 for file uploads
- All data is now per-user (multi-tenant)
- Docker Compose for local dev

## v0.6.0

_Quick Capture feature — paste a URL or note from your clipboard._

- New capture APIs for clipboard text and URLs
- Quick Capture window in the Electron shell
- Clip sources for standalone notes and excerpts

## v0.5.0

_Hybrid search and scheduled jobs._

- Semantic search via OpenAI embeddings + local vector index
- Recurring ingestion and briefing schedules
- Better settings UI for API key and model selection

## v0.4.0

_Renamed to SourceHero. Cross-platform desktop builds._

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
