# Sieve Monorepo Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the existing `client/` + empty `server/` folders into a single-repo Turborepo monorepo (`apps/client` Next.js, `apps/server` FastAPI) with bun workspaces, uv-managed Python, Postgres via Docker Compose, and generated shared API types.

**Architecture:** `apps/client` and `apps/server` are independent deployable apps orchestrated by Turborepo/bun workspaces at the root; `packages/shared-types` holds TypeScript types generated from the FastAPI OpenAPI schema and is consumed by the client as a workspace dependency. Docker Compose wires client, server, and Postgres together for local dev; native `bunx turbo dev` remains an alternative.

**Tech Stack:** Next.js 16 (bun), FastAPI + SQLAlchemy + Alembic (uv-managed), Turborepo 2, bun workspaces, PostgreSQL 16, openapi-typescript, ruff + pytest, Docker Compose.

## Global Constraints

- Single git repo at `E:\Projects\sieve` root; the existing `client/.git` (one commit, "Initial commit from Create Next App") is removed — history is discarded per user decision.
- Folder layout: `apps/client`, `apps/server`, `packages/shared-types` (Turborepo convention), not flat `client/`/`server/` at root.
- JS/TS package management: bun workspaces (`"workspaces": ["apps/*", "packages/*"]`) + Turborepo 2 for task orchestration (`dev`, `build`, `lint`).
- Python package management: `uv` with `pyproject.toml`/`uv.lock`, no poetry/pip-tools.
- `apps/server` gets a thin `package.json` shim (`dev`/`build`/`lint`/`test` scripts shelling out to `uv run ...`) purely so Turborepo can address it uniformly.
- Database: PostgreSQL 16, accessed via SQLAlchemy 2.x + `psycopg` (v3), with Alembic migration scaffolding wired to `Settings.database_url` (no concrete models/migrations yet — out of scope).
- Backend dev tooling: `ruff` (lint) + `pytest` (test), mirroring the frontend's existing `eslint`.
- Local dev: Docker Compose (`client`, `server`, `db` services) is the primary path; `bunx turbo dev` (native) is a supported secondary path.
- Shared types: `packages/shared-types` generates `src/api.d.ts` from the backend's OpenAPI schema via `openapi-typescript`, using an offline export (`python -m app.export_openapi`) — no running server required to regenerate. Regeneration is a manual command (`bun run generate-types` at root), not wired into every dev boot.
- Non-goals (do not implement): authentication, CI pipeline, production deployment config, frontend test framework, any concrete DB models/migrations beyond scaffolding.
- Tool versions confirmed available in this environment: bun 1.3.0, uv 0.8.14, docker 29.6.2, docker compose v5.3.1, python 3.12.5, git 2.54.0.

---

### Task 1: Root git repo and move client into apps/

**Files:**
- Delete: `client/.git/` (entire directory)
- Move: `client/` → `apps/client/`
- Delete: `server/` (empty placeholder; recreated properly in Task 3)
- Create: `.gitignore` (root)
- Create: `README.md` (root, minimal — expanded in Task 6)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `apps/client/` containing the full existing Next.js app (unchanged contents); a single git repo rooted at `E:\Projects\sieve` with a clean initial commit.

- [ ] **Step 1: Remove the nested git repo and empty server placeholder**

```bash
rm -rf /e/Projects/sieve/client/.git
rm -rf /e/Projects/sieve/server
```

- [ ] **Step 2: Move client into apps/**

```bash
mkdir -p /e/Projects/sieve/apps
mv /e/Projects/sieve/client /e/Projects/sieve/apps/client
```

- [ ] **Step 3: Verify the move**

Run: `ls /e/Projects/sieve/apps/client/package.json /e/Projects/sieve/apps/client/src/app/page.tsx`
Expected: both paths print (no "No such file" errors), and `ls /e/Projects/sieve/client /e/Projects/sieve/server` reports both as non-existent.

- [ ] **Step 4: Create the root .gitignore**

```
# node
node_modules/
.next/
.turbo/
out/
build/
dist/
*.tsbuildinfo
next-env.d.ts
bun-debug.log*

# python
.venv/
__pycache__/
*.pyc
*.egg-info/

# env
.env
.env.*
!.env.example

# misc
.DS_Store
*.pem
coverage/

# generated (regenerate with `bun run generate-types`)
packages/shared-types/openapi.json
```

Write this to `/e/Projects/sieve/.gitignore`.

- [ ] **Step 5: Create a minimal root README**

Write to `/e/Projects/sieve/README.md`:

```markdown
# Sieve

Monorepo: Next.js frontend (`apps/client`) + FastAPI backend (`apps/server`), orchestrated with Turborepo and bun workspaces.

Setup and usage instructions are being filled in as the project is scaffolded — see `docs/superpowers/plans/2026-08-03-monorepo-architecture.md` for the full plan.
```

- [ ] **Step 6: Init the root git repo and commit**

```bash
cd /e/Projects/sieve
git init
git add apps .gitignore README.md docs
git commit -m "chore: restructure into monorepo (apps/client, root git repo)"
```

- [ ] **Step 7: Verify**

Run: `git -C /e/Projects/sieve log --oneline` and `git -C /e/Projects/sieve status --porcelain`
Expected: one commit listed; status shows no uncommitted changes to the files just added (any files created by later tasks are fine to be untracked at this point — there are none yet).

---

### Task 2: Root workspace tooling (bun workspaces + Turborepo)

**Files:**
- Create: `package.json` (root)
- Create: `turbo.json` (root)
- Delete: `apps/client/bun.lock` (superseded by root lockfile)

**Interfaces:**
- Consumes: `apps/client/` from Task 1.
- Produces: root `bun install` / `bunx turbo run <task> --filter=<name>` works across the workspace; `apps/client`'s workspace name remains `client` (from its existing `package.json`, unchanged).

- [ ] **Step 1: Write the root package.json**

```json
{
  "name": "sieve",
  "private": true,
  "workspaces": [
    "apps/*",
    "packages/*"
  ],
  "scripts": {
    "dev": "turbo run dev",
    "build": "turbo run build",
    "lint": "turbo run lint",
    "test": "turbo run test",
    "generate-types": "bun run --cwd packages/shared-types export-schema && bun run --cwd packages/shared-types generate"
  },
  "devDependencies": {
    "turbo": "^2.3.0"
  }
}
```

Write this to `/e/Projects/sieve/package.json`. (The `generate-types` script references `packages/shared-types`, created in Task 4 — the script is inert until then, which is fine.)

- [ ] **Step 2: Write turbo.json**

```json
{
  "$schema": "https://turborepo.com/schema.json",
  "ui": "tui",
  "tasks": {
    "dev": {
      "cache": false,
      "persistent": true
    },
    "build": {
      "dependsOn": ["^build"],
      "outputs": [".next/**", "!.next/cache/**"]
    },
    "lint": {},
    "test": {}
  }
}
```

Write this to `/e/Projects/sieve/turbo.json`.

- [ ] **Step 3: Remove the app-level lockfile and install from root**

```bash
rm -f /e/Projects/sieve/apps/client/bun.lock
cd /e/Projects/sieve
bun install
```

Expected: creates `/e/Projects/sieve/bun.lock` and `/e/Projects/sieve/node_modules`, no errors.

- [ ] **Step 4: Verify Turborepo can boot the client dev server**

```bash
cd /e/Projects/sieve
bunx turbo run dev --filter=client &
TURBO_PID=$!
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000
taskkill //PID $TURBO_PID //T //F >/dev/null 2>&1 || kill $TURBO_PID
```

Expected: prints `200`. (`taskkill //T` kills the whole process tree — plain `kill` on the backgrounded shell PID won't stop the child `next dev` process it spawns on Windows, which would otherwise leave port 3000 occupied for later tasks.)

- [ ] **Step 5: Commit**

```bash
cd /e/Projects/sieve
git add package.json turbo.json bun.lock apps/client/bun.lock
git commit -m "chore: add bun workspaces + turborepo orchestration"
```

Note: `git add apps/client/bun.lock` stages its deletion since the file no longer exists on disk.

---

### Task 3: FastAPI backend scaffold (apps/server)

**Files:**
- Create: `apps/server/pyproject.toml`
- Create: `apps/server/package.json`
- Create: `apps/server/app/__init__.py`
- Create: `apps/server/app/main.py`
- Create: `apps/server/app/core/__init__.py`
- Create: `apps/server/app/core/config.py`
- Create: `apps/server/app/api/__init__.py`
- Create: `apps/server/app/api/health.py`
- Create: `apps/server/app/db/__init__.py`
- Create: `apps/server/app/db/base.py`
- Create: `apps/server/app/db/session.py`
- Create: `apps/server/app/export_openapi.py`
- Create: `apps/server/tests/__init__.py`
- Test: `apps/server/tests/test_health.py`
- Create (generated by `alembic init`): `apps/server/alembic.ini`, `apps/server/alembic/env.py`, `apps/server/alembic/script.py.mako`, `apps/server/alembic/versions/`

**Interfaces:**
- Consumes: nothing from other tasks (independent of the JS side).
- Produces:
  - `app.main:app` — the FastAPI instance, importable for `uvicorn` and `TestClient`.
  - `GET /health` → `{"status": "ok"}`.
  - `app.core.config.settings` — a `Settings` instance with `.database_url: str` and `.cors_origins: list[str]`.
  - `app.db.base.Base` — SQLAlchemy `DeclarativeBase` subclass for future models.
  - `app.db.session.get_db` — FastAPI dependency yielding a `Session`.
  - `python -m app.export_openapi <output_path>` — writes the OpenAPI schema JSON to `<output_path>` (defaults to `openapi.json` in cwd).
  - `package.json` scripts: `dev`, `build`, `lint`, `test` (all shelling to `uv`).

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "server"
version = "0.1.0"
description = "Sieve FastAPI backend"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pydantic-settings>=2.6.0",
    "sqlalchemy>=2.0.36",
    "psycopg[binary]>=3.2.3",
    "alembic>=1.13.3",
]

[dependency-groups]
dev = [
    "pytest>=8.3.3",
    "httpx>=0.27.2",
    "ruff>=0.7.4",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Write this to `/e/Projects/sieve/apps/server/pyproject.toml`.

- [ ] **Step 2: Write the package.json shim**

```json
{
  "name": "server",
  "private": true,
  "scripts": {
    "dev": "uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000",
    "build": "uv sync",
    "lint": "uv run ruff check .",
    "test": "uv run pytest"
  }
}
```

Write this to `/e/Projects/sieve/apps/server/package.json`.

- [ ] **Step 3: Write app package init files**

Write empty files:
- `/e/Projects/sieve/apps/server/app/__init__.py`
- `/e/Projects/sieve/apps/server/app/core/__init__.py`
- `/e/Projects/sieve/apps/server/app/api/__init__.py`
- `/e/Projects/sieve/apps/server/app/db/__init__.py`
- `/e/Projects/sieve/apps/server/tests/__init__.py`

- [ ] **Step 4: Write app/core/config.py**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://sieve:sieve@localhost:5432/sieve"
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
```

Write this to `/e/Projects/sieve/apps/server/app/core/config.py`.

- [ ] **Step 5: Write app/db/base.py and app/db/session.py**

`app/db/base.py`:

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

`app/db/session.py`:

```python
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

Write these to `/e/Projects/sieve/apps/server/app/db/base.py` and `/e/Projects/sieve/apps/server/app/db/session.py`.

- [ ] **Step 6: Write app/api/health.py**

```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def get_health() -> dict[str, str]:
    return {"status": "ok"}
```

Write this to `/e/Projects/sieve/apps/server/app/api/health.py`.

- [ ] **Step 7: Write app/main.py**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.core.config import settings

app = FastAPI(title="Sieve API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
```

Write this to `/e/Projects/sieve/apps/server/app/main.py`.

- [ ] **Step 8: Write the failing test**

```python
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

Write this to `/e/Projects/sieve/apps/server/tests/test_health.py`. (It won't actually fail since Step 6/7 already implement the endpoint — run it now to confirm the whole chain works end to end.)

- [ ] **Step 9: Install dependencies and run the test**

```bash
cd /e/Projects/sieve/apps/server
uv sync
uv run pytest -v
```

Expected: `test_health_returns_ok PASSED`, and `uv.lock` is created in `apps/server/`.

- [ ] **Step 10: Write app/export_openapi.py**

```python
import json
import sys

from app.main import app


def main() -> None:
    schema = app.openapi()
    output = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"
    with open(output, "w") as f:
        json.dump(schema, f, indent=2)


if __name__ == "__main__":
    main()
```

Write this to `/e/Projects/sieve/apps/server/app/export_openapi.py`.

- [ ] **Step 11: Verify the export script**

```bash
cd /e/Projects/sieve/apps/server
uv run python -m app.export_openapi /tmp/openapi-check.json
grep -o '"/health"' /tmp/openapi-check.json
```

Expected: prints `"/health"`. Then delete the scratch file: `rm /tmp/openapi-check.json`.

- [ ] **Step 12: Initialize Alembic scaffolding**

```bash
cd /e/Projects/sieve/apps/server
uv run alembic init alembic
```

Expected: creates `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/` (empty).

- [ ] **Step 13: Wire alembic/env.py to Settings and Base**

In the generated `/e/Projects/sieve/apps/server/alembic/env.py`, apply these two edits (this is Alembic's standard generated template as of 1.13+; the anchor lines below are what `alembic init` produces):

Edit 1 — old:
```python
from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
```
new:
```python
from alembic import context

from app.core.config import settings
from app.db.base import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
```

Edit 2 — old: `target_metadata = None`
new: `target_metadata = Base.metadata`

- [ ] **Step 14: Verify Alembic loads the config without error**

```bash
cd /e/Projects/sieve/apps/server
uv run alembic check
```

Expected: exits without a Python traceback (it will report "No new upgrade operations detected" or similar, since there are no models yet — that's correct, not an error).

- [ ] **Step 15: Verify Turborepo can boot the server dev process too**

```bash
cd /e/Projects/sieve
bun install
bunx turbo run dev --filter=server &
TURBO_PID=$!
sleep 5
curl -s http://localhost:8000/health
taskkill //PID $TURBO_PID //T //F >/dev/null 2>&1 || kill $TURBO_PID
```

Expected: prints `{"status":"ok"}`.

- [ ] **Step 16: Commit**

```bash
cd /e/Projects/sieve
git add apps/server bun.lock
git commit -m "feat: scaffold FastAPI backend with health endpoint, SQLAlchemy/Alembic scaffolding"
```

---

### Task 4: Shared TypeScript types generated from the OpenAPI schema

**Files:**
- Create: `packages/shared-types/package.json`
- Create: `packages/shared-types/src/api.d.ts` (generated output, committed)

**Interfaces:**
- Consumes: `apps/server/app/export_openapi.py` (Task 3) to produce the schema; root `package.json`'s `generate-types` script (Task 2) invokes this package's scripts.
- Produces: `packages/shared-types` workspace package with `exports["."].types = "./src/api.d.ts"`, importable as `import type { paths } from "shared-types"`. `paths["/health"]["get"]["responses"][200]["content"]["application/json"]` types the health response as `{ status: string }`.

- [ ] **Step 1: Write packages/shared-types/package.json**

```json
{
  "name": "shared-types",
  "private": true,
  "version": "0.1.0",
  "exports": {
    ".": {
      "types": "./src/api.d.ts"
    }
  },
  "scripts": {
    "export-schema": "cd ../../apps/server && uv run python -m app.export_openapi ../../packages/shared-types/openapi.json",
    "generate": "openapi-typescript openapi.json -o src/api.d.ts"
  },
  "devDependencies": {
    "openapi-typescript": "^7.4.0"
  }
}
```

Write this to `/e/Projects/sieve/packages/shared-types/package.json`.

- [ ] **Step 2: Install the new workspace member**

```bash
cd /e/Projects/sieve
bun install
```

Expected: `openapi-typescript` installed under root `node_modules`, no errors.

- [ ] **Step 3: Run the generation pipeline**

```bash
cd /e/Projects/sieve
bun run generate-types
```

Expected: creates `packages/shared-types/openapi.json` and `packages/shared-types/src/api.d.ts`.

- [ ] **Step 4: Verify the generated types reference /health**

Run: `grep -o '"/health"' /e/Projects/sieve/packages/shared-types/src/api.d.ts`
Expected: prints `"/health"`.

- [ ] **Step 5: Commit**

```bash
cd /e/Projects/sieve
git add package.json packages/shared-types bun.lock
git commit -m "feat: add shared-types package generating TS types from FastAPI OpenAPI schema"
```

Note: `packages/shared-types/openapi.json` is listed in the root `.gitignore` (Task 1, Step 4) and won't be staged; `src/api.d.ts` is not ignored and is committed.

---

### Task 5: Frontend integration (typed health check on the home page)

**Files:**
- Modify: `apps/client/package.json` (add `shared-types` workspace dependency)
- Create: `apps/client/src/lib/api.ts`
- Modify: `apps/client/src/app/page.tsx`

**Interfaces:**
- Consumes: `shared-types`'s `paths` type (Task 4).
- Produces: `getHealth(): Promise<{ status: string }>` from `apps/client/src/lib/api.ts`, used by the home page.

- [ ] **Step 1: Add the shared-types dependency**

In `/e/Projects/sieve/apps/client/package.json`, add a `dependencies` entry:

```json
"shared-types": "workspace:*"
```

(Insert it alphabetically among the existing `dependencies`: `next`, `react`, `react-dom`, `shared-types`.)

- [ ] **Step 2: Install to link the workspace package**

```bash
cd /e/Projects/sieve
bun install
```

Expected: no errors; `apps/client/node_modules/shared-types` resolves (via bun's workspace linking) to `packages/shared-types`.

- [ ] **Step 3: Write apps/client/src/lib/api.ts**

```typescript
import type { paths } from "shared-types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type HealthResponse =
  paths["/health"]["get"]["responses"][200]["content"]["application/json"];

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_URL}/health`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`);
  }
  return response.json();
}
```

Write this to `/e/Projects/sieve/apps/client/src/lib/api.ts`.

- [ ] **Step 4: Replace the home page with a minimal backend-status page**

Replace the full contents of `/e/Projects/sieve/apps/client/src/app/page.tsx` with:

```tsx
import { getHealth } from "@/lib/api";

export default async function Home() {
  let status: string;
  try {
    const health = await getHealth();
    status = health.status;
  } catch {
    status = "unreachable";
  }

  return (
    <div className="flex flex-col flex-1 items-center justify-center gap-4 bg-zinc-50 font-sans dark:bg-black">
      <h1 className="text-3xl font-semibold text-black dark:text-zinc-50">
        Sieve
      </h1>
      <p className="text-lg text-zinc-600 dark:text-zinc-400">
        Backend status: <span data-testid="backend-status">{status}</span>
      </p>
    </div>
  );
}
```

- [ ] **Step 5: Verify end-to-end with both dev servers running**

```bash
cd /e/Projects/sieve
bunx turbo run dev --filter=server &
SERVER_PID=$!
sleep 3
bunx turbo run dev --filter=client &
CLIENT_PID=$!
sleep 5
curl -s http://localhost:3000 | grep -o 'Backend status:[^<]*<span data-testid="backend-status">[a-z]*'
taskkill //PID $CLIENT_PID //T //F >/dev/null 2>&1 || kill $CLIENT_PID
taskkill //PID $SERVER_PID //T //F >/dev/null 2>&1 || kill $SERVER_PID
```

Expected: output includes `backend-status">ok`, confirming the client successfully called the live backend and rendered the typed response.

- [ ] **Step 6: Commit**

```bash
cd /e/Projects/sieve
git add apps/client bun.lock
git commit -m "feat: render live backend health status on the client home page"
```

---

### Task 6: Docker Compose for local dev (client + server + Postgres)

**Files:**
- Create: `apps/server/Dockerfile`
- Create: `apps/client/Dockerfile`
- Create: `docker-compose.yml` (root)
- Create: `.env.example` (root)
- Modify: `README.md` (root — fill in real dev instructions)

**Interfaces:**
- Consumes: `apps/client`, `apps/server`, `packages/shared-types` (all prior tasks).
- Produces: `docker compose up` boots `db` (Postgres 16), `server` (FastAPI on :8000), `client` (Next.js on :3000) with the client able to reach the server.

- [ ] **Step 1: Write apps/server/Dockerfile**

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY app ./app

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

Write this to `/e/Projects/sieve/apps/server/Dockerfile`.

- [ ] **Step 2: Write apps/client/Dockerfile**

This one builds from the repo root so bun can resolve the `shared-types` workspace dependency.

```dockerfile
FROM oven/bun:1 AS base
WORKDIR /repo

COPY package.json bun.lock ./
COPY apps/client/package.json apps/client/package.json
COPY packages/shared-types/package.json packages/shared-types/package.json
RUN bun install --frozen-lockfile

COPY apps/client apps/client
COPY packages/shared-types packages/shared-types

WORKDIR /repo/apps/client
EXPOSE 3000
CMD ["bun", "run", "dev"]
```

Write this to `/e/Projects/sieve/apps/client/Dockerfile`.

- [ ] **Step 3: Write docker-compose.yml**

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: sieve
      POSTGRES_PASSWORD: sieve
      POSTGRES_DB: sieve
    ports:
      - "5432:5432"
    volumes:
      - db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sieve"]
      interval: 5s
      timeout: 5s
      retries: 5

  server:
    build:
      context: ./apps/server
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+psycopg://sieve:sieve@db:5432/sieve
    volumes:
      - ./apps/server/app:/app/app
    depends_on:
      db:
        condition: service_healthy

  client:
    build:
      context: .
      dockerfile: apps/client/Dockerfile
    ports:
      - "3000:3000"
    environment:
      NEXT_PUBLIC_API_URL: http://server:8000
    volumes:
      - ./apps/client/src:/repo/apps/client/src
    depends_on:
      - server

volumes:
  db-data:
```

Write this to `/e/Projects/sieve/docker-compose.yml`.

- [ ] **Step 4: Write .env.example**

```
DATABASE_URL=postgresql+psycopg://sieve:sieve@localhost:5432/sieve
NEXT_PUBLIC_API_URL=http://localhost:8000
POSTGRES_USER=sieve
POSTGRES_PASSWORD=sieve
POSTGRES_DB=sieve
```

Write this to `/e/Projects/sieve/.env.example`.

- [ ] **Step 5: Build and boot the stack**

```bash
cd /e/Projects/sieve
docker compose up -d --build
sleep 10
curl -s http://localhost:8000/health
curl -s http://localhost:3000 | grep -o 'backend-status">[a-z]*'
```

Expected: first curl prints `{"status":"ok"}`; second prints `backend-status">ok`.

- [ ] **Step 6: Tear down**

```bash
cd /e/Projects/sieve
docker compose down -v
```

- [ ] **Step 7: Fill in the root README**

Replace the contents of `/e/Projects/sieve/README.md` with:

```markdown
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
```

- [ ] **Step 8: Commit**

```bash
cd /e/Projects/sieve
git add apps/server/Dockerfile apps/client/Dockerfile docker-compose.yml .env.example README.md
git commit -m "feat: add Docker Compose local dev environment (client, server, postgres)"
```

---

## Final Verification

- [ ] `git -C /e/Projects/sieve log --oneline` shows 6 commits, working tree clean (`git status --porcelain` empty).
- [ ] `docker compose up -d --build && curl localhost:8000/health && curl localhost:3000` (per Task 6 Step 5) succeeds.
- [ ] `cd apps/server && uv run pytest` passes.
- [ ] `bun run generate-types` regenerates `packages/shared-types/src/api.d.ts` without error.
