# Sieve

Monorepo: Next.js frontend (`apps/client`) + FastAPI backend (`apps/server`), orchestrated with Turborepo and bun workspaces.

## Local development

**Option 1 — Docker Compose (recommended):**

```bash
docker compose up
```

No `.env` file is needed — `docker-compose.yml` hardcodes all environment
values directly (there's no `env_file:` or `${VAR}` substitution), so a root
`.env` has zero effect on this path.

- Client: http://localhost:3000
- Server: http://localhost:8000 (docs at /docs)

Note: the `db` service (Postgres) is published on host port `5433` (not the
default `5432`) to avoid colliding with a native Postgres install that may
already be listening on `5432`. Container-to-container traffic between
`server` and `db` still uses port `5432` internally — this only affects how
you reach Postgres from the host, e.g. `psql -h localhost -p 5433 -U sieve`.

The `ingest` service runs the Caspian message listener as its own process
(separate from `server`, so an API reload never disrupts the live Caspian
connection). It needs real Caspian credentials: copy `.env.example` to a
root `.env` (gitignored) and fill in `CASPIAN_API_KEY`, `CASPIAN_BASE_URL`,
`TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`. Unlike the rest of this file's
hardcoded dev values, these four are read from that root `.env` via Docker
Compose's built-in `${VAR}` substitution, since they're real secrets that
must never be committed.

**Option 2 — native:**

```bash
bun install
bunx turbo dev
```

Requires `uv` installed locally and a Postgres instance matching `DATABASE_URL`.
If you want to override the built-in defaults, copy `.env.example` to
`apps/server/.env` — that's where FastAPI's settings loader looks (it reads
`.env` relative to `apps/server`, which is the working directory Turborepo
runs the server's `dev` script from), not a root-level `.env`. Likewise,
Next.js only reads `.env*` files from `apps/client/`, not the repo root.

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
