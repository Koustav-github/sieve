# Sieve Monorepo Architecture — Design

Date: 2026-08-03

## Context

The project currently has a `client/` folder (a scaffolded Next.js app, bootstrapped via `create-next-app`, using bun, with its own separate `.git` repo) and an empty `server/` folder. The root `sieve/` directory itself is not yet a git repo. The goal is to establish a proper monorepo architecture housing a Next.js frontend and a FastAPI backend.

## Goals

- Single git repo at the root managing both frontend and backend.
- Turborepo-orchestrated monorepo (bun workspaces) so both apps can be run/built through one set of commands.
- FastAPI backend managed with `uv`.
- Local dev environment via Docker Compose (client, server, Postgres).
- Typed API contract: TS types generated from FastAPI's OpenAPI schema, consumed by the frontend.

## Non-goals

- Authentication/authorization scaffolding.
- CI/CD pipeline configuration.
- Production deployment configuration (hosting, env secrets management beyond `.env.example`).
- Automatic (on-every-dev-boot) type regeneration — regeneration is a manual step for now.

## Folder Architecture

```
sieve/
├── apps/
│   ├── client/                 # existing Next.js app, moved here as-is
│   │   ├── src/
│   │   ├── public/
│   │   ├── package.json
│   │   └── ...
│   └── server/                 # new FastAPI app
│       ├── app/
│       │   ├── main.py
│       │   ├── api/            # routers
│       │   ├── core/           # config, settings
│       │   ├── db/             # session, models
│       │   └── schemas/        # pydantic models
│       ├── pyproject.toml      # uv-managed
│       ├── uv.lock
│       └── package.json        # thin wrapper so Turborepo can run dev/build via uv
├── packages/
│   └── shared-types/           # generated TS types from FastAPI's OpenAPI schema
│       └── package.json
├── docker-compose.yml          # client + server + postgres
├── turbo.json
├── package.json                 # root, bun workspaces (apps/*, packages/*)
├── .gitignore
├── .env.example
└── README.md
```

Each unit has one clear responsibility: `apps/client` is the UI, `apps/server` is the API, `packages/shared-types` is the generated contract between them.

## Git & Tooling Setup

- `client/.git` is removed (existing history discarded); root `sieve/` becomes the single git repo for the whole monorepo.
- Root `package.json` declares bun workspaces: `"workspaces": ["apps/*", "packages/*"]`.
- `turbo.json` defines pipeline tasks: `dev` (persistent, uncached), `build`, `lint`.
- `apps/server/package.json` is a thin shim exposing `dev`/`build` scripts that shell out to `uv` (e.g. `"dev": "uv run uvicorn app.main:app --reload"`, `"build": "uv sync"`), so Turborepo can address the Python app uniformly alongside the Next.js app. Actual Python dependency resolution is owned by `uv` via `pyproject.toml`/`uv.lock`.
- Root `.gitignore` covers both ecosystems: `node_modules`, `.next`, `.venv`, `__pycache__`, `.env*`, `*.pyc`, etc.
- Backend dev dependencies: `ruff` (lint/format) and `pytest`, mirroring the frontend's existing `eslint` setup. Kept minimal — no extra plugins beyond sane defaults.

## Docker Compose & Dev Workflow

`docker-compose.yml` at root defines three services:

- **client** — builds from `apps/client`, runs `bun dev`, port 3000, volume-mounted for hot reload.
- **server** — builds from `apps/server`, runs `uv run uvicorn app.main:app --reload`, port 8000, volume-mounted for reload.
- **db** — `postgres:16`, named volume for persistence, port 5432, configured via `.env`. `server` depends on `db` with a healthcheck condition.

Each app has its own lightweight multi-stage `Dockerfile` (deps stage → dev stage). Root `.env.example` documents required variables (`DATABASE_URL`, `NEXT_PUBLIC_API_URL`, etc.).

Two supported dev paths:
1. `docker compose up` — fully containerized, consistent across machines.
2. `bunx turbo dev` — native run (bun dev + uv run uvicorn) for those who prefer not to use Docker locally.

## Shared Types Workflow

`packages/shared-types` contains a script that generates TypeScript types from the FastAPI OpenAPI schema using `openapi-typescript`. Two schema sources are supported:
- Live: point at the running server's `/openapi.json`.
- Static: a small backend command (`uv run python -m app.export_openapi`) writes the schema to a file, so generation works without the server running (useful in CI or offline).

Output lands at `packages/shared-types/src/api.d.ts`. `apps/client` depends on `shared-types` as a workspace package and imports these generated types for its API calls.

Regeneration is manual (`bun run generate-types` at the root) — not wired into every dev boot, to avoid requiring the server to always be running just to start the frontend. Developers re-run it after changing backend routes/schemas.

## Testing

- Frontend: existing `eslint` config stays; no test framework added beyond what's already scaffolded (out of scope to add one now — can be a follow-up).
- Backend: `pytest` configured with a `tests/` directory under `apps/server`, a basic health-check endpoint test as the initial example.

## Open Follow-ups (explicitly deferred)

- CI pipeline (lint/build/test on push).
- Auth.
- Production deployment configuration.
- Frontend test framework.
