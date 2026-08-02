# Sieve

Monorepo: Next.js frontend (`apps/client`) + FastAPI backend (`apps/server`), orchestrated with Turborepo and bun workspaces.

## Local development

**Option 1 — Docker Compose (recommended):**

```bash
cp .env.example .env
docker compose up
```

- Client: http://localhost:3000
- Server: http://localhost:8000 (docs at /docs)

Note: the `db` service (Postgres) is published on host port `5433` (not the
default `5432`) to avoid colliding with a native Postgres install that may
already be listening on `5432`. Container-to-container traffic between
`server` and `db` still uses port `5432` internally — this only affects how
you reach Postgres from the host, e.g. `psql -h localhost -p 5433 -U sieve`.

**Option 2 — native:**

```bash
bun install
bunx turbo dev
```

Requires `uv` installed locally and a Postgres instance matching `DATABASE_URL` in `.env`.

## Regenerating shared API types

After changing backend routes/schemas:

```bash
bun run generate-types
```

This regenerates `packages/shared-types/src/api.d.ts` from the FastAPI OpenAPI schema.

## Backend tests / lint

```bash
cd apps/server
uv run pytest
uv run ruff check .
```
