#!/usr/bin/env bash
set -e

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
SQL

psql \
  --username "$POSTGRES_USER" \
  --dbname postgres \
  -v ON_ERROR_STOP=1 \
  -v n8n_db="$N8N_DB" <<'SQL'
SELECT format('CREATE DATABASE %I', :'n8n_db')
WHERE NOT EXISTS (
  SELECT FROM pg_database WHERE datname = :'n8n_db'
)
\gexec
SQL
