-- Extensions for the main database.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Test database (used by `make test`; tests drop and recreate its schema anyway).
CREATE DATABASE sourcehero_test;
\connect sourcehero_test
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
