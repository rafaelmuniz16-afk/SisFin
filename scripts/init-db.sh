#!/bin/sh
set -eu
# Papel de aplicação não pode ignorar RLS nem executar DDL.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=app_password="$DB_APP_PASSWORD" <<'SQL'
CREATE ROLE sisfin_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA public TO sisfin_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO sisfin_app;
SQL
