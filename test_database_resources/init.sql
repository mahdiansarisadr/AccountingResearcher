-- Runs once on first container start (via docker-entrypoint-initdb.d).
-- Enables pgvector and provisions a least-privilege, read-only role for the agent.

CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ar_readonly') THEN
        CREATE ROLE ar_readonly LOGIN PASSWORD 'ar_readonly';
    END IF;
END
$$;

-- Keep the agent's queries bounded (defense-in-depth for the latency budget).
ALTER ROLE ar_readonly SET statement_timeout = '8000ms';

GRANT CONNECT ON DATABASE accounting TO ar_readonly;
GRANT USAGE ON SCHEMA public TO ar_readonly;

-- The agent role may only ever read.
REVOKE CREATE ON SCHEMA public FROM ar_readonly;

-- Tables created later by ar_admin become readable by ar_readonly automatically.
ALTER DEFAULT PRIVILEGES FOR ROLE ar_admin IN SCHEMA public
    GRANT SELECT ON TABLES TO ar_readonly;
