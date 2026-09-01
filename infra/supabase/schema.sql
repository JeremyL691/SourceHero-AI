-- SourceHero AI - PostgreSQL Schema
-- Run this on Supabase or local PostgreSQL with pgvector extension

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- Users table (managed by Supabase Auth, but we keep a profile)
-- ============================================================
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    display_name TEXT,
    avatar_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- Core tables
-- ============================================================

CREATE TABLE IF NOT EXISTS sources (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    source_type VARCHAR(32) NOT NULL,
    name VARCHAR(255) NOT NULL,
    url TEXT,
    local_path TEXT,
    r2_key TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_ingested_at TIMESTAMPTZ
);

CREATE INDEX idx_sources_user_id ON sources(user_id);
CREATE INDEX idx_sources_source_type ON sources(source_type);
CREATE INDEX idx_sources_status ON sources(status);

CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    title VARCHAR(512) NOT NULL,
    url TEXT,
    author VARCHAR(255),
    published_at VARCHAR(64),
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content_hash VARCHAR(64) NOT NULL,
    raw_text TEXT NOT NULL,
    clean_text TEXT NOT NULL
);

CREATE INDEX idx_documents_source_id ON documents(source_id);
CREATE INDEX idx_documents_user_id ON documents(user_id);
CREATE UNIQUE INDEX idx_documents_content_hash ON documents(user_id, content_hash);

CREATE TABLE IF NOT EXISTS document_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    chunk_hash VARCHAR(64) NOT NULL,
    token_estimate INTEGER NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    embedding_id VARCHAR(128),
    embedding vector(1536)
);

CREATE INDEX idx_document_chunks_document_id ON document_chunks(document_id);
CREATE INDEX idx_document_chunks_user_id ON document_chunks(user_id);
CREATE UNIQUE INDEX idx_document_chunks_chunk_hash ON document_chunks(user_id, chunk_hash);

-- HNSW index for vector similarity search
CREATE INDEX idx_document_chunks_embedding ON document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    documents_found INTEGER DEFAULT 0,
    documents_inserted INTEGER DEFAULT 0,
    chunks_inserted INTEGER DEFAULT 0,
    duplicates_skipped INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE INDEX idx_ingestion_runs_source_id ON ingestion_runs(source_id);
CREATE INDEX idx_ingestion_runs_user_id ON ingestion_runs(user_id);

CREATE TABLE IF NOT EXISTS briefings (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    answer_markdown TEXT NOT NULL,
    citation_json TEXT DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_briefings_user_id ON briefings(user_id);

CREATE TABLE IF NOT EXISTS collections (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, name)
);

CREATE INDEX idx_collections_user_id ON collections(user_id);

CREATE TABLE IF NOT EXISTS tags (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name VARCHAR(80) NOT NULL,
    color VARCHAR(32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, name)
);

CREATE INDEX idx_tags_user_id ON tags(user_id);

CREATE TABLE IF NOT EXISTS collection_items (
    id BIGSERIAL PRIMARY KEY,
    collection_id BIGINT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    item_type VARCHAR(32) NOT NULL,
    item_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(collection_id, item_type, item_id)
);

CREATE INDEX idx_collection_items_collection_id ON collection_items(collection_id);

CREATE TABLE IF NOT EXISTS item_tags (
    id BIGSERIAL PRIMARY KEY,
    tag_id BIGINT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    item_type VARCHAR(32) NOT NULL,
    item_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tag_id, item_type, item_id)
);

CREATE INDEX idx_item_tags_tag_id ON item_tags(tag_id);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    job_type VARCHAR(32) NOT NULL,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    schedule_kind VARCHAR(32) NOT NULL,
    time_local VARCHAR(8) NOT NULL,
    day_of_week INTEGER,
    payload_json TEXT DEFAULT '{}',
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ NOT NULL,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_scheduled_jobs_user_id ON scheduled_jobs(user_id);
CREATE INDEX idx_scheduled_jobs_next_run_at ON scheduled_jobs(next_run_at);

CREATE TABLE IF NOT EXISTS scheduled_job_runs (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES scheduled_jobs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    summary TEXT,
    error_message TEXT
);

CREATE INDEX idx_scheduled_job_runs_job_id ON scheduled_job_runs(job_id);

-- ============================================================
-- Row Level Security (RLS) policies for Supabase
-- ============================================================

ALTER TABLE sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE briefings ENABLE ROW LEVEL SECURITY;
ALTER TABLE collections ENABLE ROW LEVEL SECURITY;
ALTER TABLE tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE collection_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE item_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduled_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduled_job_runs ENABLE ROW LEVEL SECURITY;

-- Users can only access their own data
CREATE POLICY "Users can view own sources" ON sources FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own sources" ON sources FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update own sources" ON sources FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "Users can delete own sources" ON sources FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "Users can view own documents" ON documents FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own documents" ON documents FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update own documents" ON documents FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "Users can delete own documents" ON documents FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "Users can view own chunks" ON document_chunks FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own chunks" ON document_chunks FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update own chunks" ON document_chunks FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "Users can delete own chunks" ON document_chunks FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "Users can view own ingestion_runs" ON ingestion_runs FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own ingestion_runs" ON ingestion_runs FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can view own briefings" ON briefings FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own briefings" ON briefings FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can delete own briefings" ON briefings FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "Users can view own collections" ON collections FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own collections" ON collections FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update own collections" ON collections FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "Users can delete own collections" ON collections FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "Users can view own tags" ON tags FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own tags" ON tags FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update own tags" ON tags FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "Users can delete own tags" ON tags FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "Users can view own collection_items" ON collection_items FOR SELECT USING (
    EXISTS (SELECT 1 FROM collections WHERE id = collection_id AND user_id = auth.uid())
);
CREATE POLICY "Users can insert own collection_items" ON collection_items FOR INSERT WITH CHECK (
    EXISTS (SELECT 1 FROM collections WHERE id = collection_id AND user_id = auth.uid())
);
CREATE POLICY "Users can delete own collection_items" ON collection_items FOR DELETE USING (
    EXISTS (SELECT 1 FROM collections WHERE id = collection_id AND user_id = auth.uid())
);

CREATE POLICY "Users can view own item_tags" ON item_tags FOR SELECT USING (
    EXISTS (SELECT 1 FROM tags WHERE id = tag_id AND user_id = auth.uid())
);
CREATE POLICY "Users can insert own item_tags" ON item_tags FOR INSERT WITH CHECK (
    EXISTS (SELECT 1 FROM tags WHERE id = tag_id AND user_id = auth.uid())
);
CREATE POLICY "Users can delete own item_tags" ON item_tags FOR DELETE USING (
    EXISTS (SELECT 1 FROM tags WHERE id = tag_id AND user_id = auth.uid())
);

CREATE POLICY "Users can view own scheduled_jobs" ON scheduled_jobs FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own scheduled_jobs" ON scheduled_jobs FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update own scheduled_jobs" ON scheduled_jobs FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "Users can delete own scheduled_jobs" ON scheduled_jobs FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "Users can view own scheduled_job_runs" ON scheduled_job_runs FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own scheduled_job_runs" ON scheduled_job_runs FOR INSERT WITH CHECK (auth.uid() = user_id);
