# Sieve

Monorepo: Next.js frontend (`apps/client`) + FastAPI backend (`apps/server`), orchestrated with Turborepo and bun workspaces.

## Local development

**Option 1 — Docker Compose (recommended):**

```bash
docker compose up
```

`db`, `server`, and `client` need no `.env` file — `docker-compose.yml`
hardcodes all of their environment values directly (there's no `env_file:`
or `${VAR}` substitution on those three services), so a root `.env` has zero
effect on them. The `ingest` service is the exception - see below.

A one-shot `migrate` service runs `alembic upgrade head` before `server` and
`ingest` start (they both `depends_on: migrate: condition:
service_completed_successfully`), so a fresh `docker compose up` gets a
database with tables with no manual step.

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

If you're not working on ingestion (e.g. a frontend dev doing `docker
compose up` with no root `.env` at all), `db`, `server`, and `client` come up
fine regardless. `ingest` will still fail fast: with no `CASPIAN_API_KEY` at
all, `CommClient()` itself raises before the process gets anywhere near
`register_identities()` or `listen()` - there's no valid way to run the
Caspian listener without a real key, so this is expected, not a bug. Thanks
to the `register_identities()` fix that made per-channel registration
failures (and blank bot tokens) non-fatal, `ingest` crash-looping (`restart:
unless-stopped`) is now scoped to "no/invalid `CASPIAN_API_KEY`" rather than
also happening on ordinary restarts (previously any transient 409 or blank
token would crash-loop it too). It does not affect `server`/`client`/`db` -
they don't depend on `ingest` and keep working normally. If `ingest`'s
crash-loop noise bothers you and you're not touching ingestion, run `docker
compose up server client db` instead of `docker compose up`.

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
