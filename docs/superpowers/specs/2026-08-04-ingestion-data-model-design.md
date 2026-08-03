# Sieve Backend — Ingestion + Core Data Model — Design

Date: 2026-08-04

## Context

The repo currently has a scaffolded monorepo (`apps/client` Next.js, `apps/server` FastAPI with only a `/health` endpoint, `packages/shared-types`, Docker Compose for client+server+Postgres) but none of the actual Sieve product. Sieve is a communication router built on Caspian (a hosted channel-adapter gateway for email/Telegram/Slack/Discord), specified in `apps/client/Sieve_Project_Spec.docx`. This is the first of several sub-projects that together build out the backend described in the spec; the full backend (ingestion, classification cascade, policy layer, delivery, audit, dashboard API) is too broad for a single design, so it's being decomposed and brainstormed sub-project by sub-project, following the spec's own day-by-day phasing.

This sub-project covers spec §4 stages 1–3 (ingest, resolve sender, coarse bucket) and the first 3 of the 8 tables in spec §8 (`messages`, `person_entities`, `channel_handles`). It is the foundation every later sub-project (classification, policy, delivery, dashboard) depends on.

## Goals

- Real `caspian-sdk` integration (PyPI package, confirmed to exist) registering the 3 agent identities from spec §3 (`careers`, `support`, `internal`) on one `CommClient`, one `on_message` handler, one `listen()` loop — per spec §3's implementation note, not one listener per identity.
- Persist every inbound message with dedup, resolve the sender against known people (creating provisional entities on a miss), and stamp the coarse bucket (the arrival identity — free and deterministic per spec §3).
- Keep the ingestion process isolated from the FastAPI web process so a crash or reload in one doesn't affect the other.
- Grow the DB schema feature-by-feature: only the 3 tables this sub-project needs, not all 8 from spec §8 up front.

## Non-goals (deferred to later sub-projects)

- Classification cascade (L1/L2/L3), subject extraction, visibility/policy engine, urgency/budget arbitration, outbound delivery, audit trail beyond the raw `messages` row, dashboard API, cross-channel identity resolution (explicitly a stretch goal in the spec).
- LangChain/LangGraph — not needed until the classification cascade sub-project.
- CI pipeline, auth, production deployment config (existing non-goals from the monorepo scaffold, still deferred).

## Tooling decisions

- **Python env**: keep `uv` (already used by `apps/server`). `uv sync`/`uv run` already manage an isolated `.venv` per project — no need for manual `python -m venv` + `pip`.
- **caspian-sdk**: install via `uv add caspian-sdk` (public PyPI package, confirmed to exist, MIT licensed, `pip install caspian-sdk`, Python ≥3.10). This is a hard requirement — the real SDK is used directly, not a mock/stub standing in as the implementation.
- **Credentials**: real Caspian API key/base URL already available; wired into `.env` (`CASPIAN_API_KEY`, `CASPIAN_BASE_URL`), matching the SDK's default `CommClient()` behavior of reading them from the environment.

## Architecture & components

```
apps/server/
├── app/
│   ├── ingest/
│   │   ├── __init__.py
│   │   ├── worker.py        # entrypoint: builds CommClient, registers on_message, calls listen()
│   │   ├── handler.py       # on_message body: dedup -> persist -> resolve sender -> coarse bucket
│   │   └── identities.py    # the 3 identity registrations (careers/support/internal) + channel connects
│   ├── models/
│   │   ├── message.py       # Message ORM model
│   │   ├── person.py        # PersonEntity ORM model
│   │   └── channel_handle.py
│   ├── db/                  # existing: base.py, session.py — unchanged
│   └── main.py               # existing FastAPI app — untouched by this sub-project
├── alembic/versions/         # first real migration lands here
```

`app/ingest/worker.py` is a new container entrypoint (`python -m app.ingest.worker`), run by a new `ingest` service in `docker-compose.yml`: same build context/image as `server`, overridden `command`, same `DATABASE_URL`, plus `CASPIAN_API_KEY`/`CASPIAN_BASE_URL`. It shares `app.db.session.SessionLocal` and the ORM models with the FastAPI app — one codebase, one schema, two processes.

**Why a separate process from the FastAPI server:** `client.listen()` is a blocking loop, not a webhook route FastAPI can mount. Running it in-process (e.g. a background thread on FastAPI startup) would mean `uvicorn --reload` — already configured for dev — tears down and reopens the Caspian connection on every unrelated file save, and a bug in message handling could take the HTTP API down with it. A separate worker process keeps the API server purely request/response and isolates ingestion failures from it. This still satisfies spec §3's constraint of "one process, one handler, one listen() loop" — that constraint is about not forking a listener per identity, not about co-locating with the web server.

## Data flow (spec §4, stages 1–3)

1. Caspian delivers an inbound message → `client.listen()` dispatches to the single `@client.on_message` handler.
2. Handler reads `message.sender` (dict with `address`), `message.text`, channel, `identity`/`agent_id`, `thread_id`, and the message's Caspian-assigned id.
3. **Dedup check**: look up by `caspian_message_id`. If a row already exists, return immediately — `listen()` reconnects can redeliver, so this must be idempotent.
4. **Persist**: insert a `messages` row — raw payload (JSON), channel, agent_id, sender handle, thread id, received timestamp.
5. **Resolve sender**: look up `sender.address` in `channel_handles` for this channel. Hit → attach the existing `person_entities.id`. Miss → create a new `person_entities` row (`is_provisional=True`) plus the new `channel_handles` mapping.
6. **Coarse bucket**: `agent_id` (`careers`/`support`/`internal`) is stored directly on the message row — it *is* the coarse bucket per spec §3, no separate lookup needed.

Nothing past stage 3 (classification, subject extraction, visibility, budget, delivery, audit) is in scope here.

## Data model (spec §8, first 3 of 8 tables)

```
messages
  id                    PK
  caspian_message_id    UNIQUE, dedup key
  agent_id              careers | support | internal
  channel               email | telegram | slack | discord
  sender_handle         text
  thread_id             text, nullable
  raw_payload           JSON
  received_at           timestamp

person_entities
  id             PK
  display_name   text, nullable
  is_provisional bool

channel_handles
  id                PK
  person_entity_id  FK -> person_entities.id
  channel           text
  handle            text
  UNIQUE(channel, handle)
```

The remaining five spec §8 tables (`org_roles`, `buckets`, `rules`, `routing_decisions`, `overrides`) are **not** created yet. Each lands with the sub-project that actually consumes it, so the schema grows feature-by-feature rather than being scaffolded empty up front.

## Error handling

- **Malformed/unexpected message** (missing sender, empty text, etc.): caught per-message inside the handler, logged at ERROR with the raw payload and Caspian message id, loop continues. One bad message must never take down ingestion for all three identities.
- **Duplicate delivery**: the app-level dedup check is backed by a DB-level `UNIQUE` constraint on `caspian_message_id` as a second line of defense — an `IntegrityError` on insert is treated as "already processed," not a crash.
- **Caspian connection drops**: reconnects/retries are owned by the SDK internally (spec §2), not implemented here. If `listen()` throws unrecoverably, the process exits and Docker's `unless-stopped` restart policy brings the worker back up; in-flight message loss on a hard crash is an accepted risk at this stage.
- **DB unreachable**: same per-message try/except — logged, loop continues, that message is dropped. There's no application-level ack/retry contract with Caspian to lean on for redelivery guarantees.

## Testing

- **Handler unit tests**: call the `on_message` handler function directly with a small fake `message` object (just the fields actually read) — no live Caspian connection needed. Covers: new message persisted; duplicate id is a no-op; unknown sender creates a provisional `person_entities` row; known sender attaches to the existing one.
- **DB**: SQLite in-memory engine for these tests, via a new `apps/server/tests/conftest.py` fixture — separate from the real Postgres used at runtime. Uses the existing `pytest` + `httpx` setup already in `pyproject.toml`.
- **Live smoke test**: manual, not part of the automated suite. Since real Caspian credentials are available, one real end-to-end check (send a message on each of the 4 channels, confirm rows land in Postgres) happens after implementation, mirroring the spec's own Day 1–2 exit criteria. Not automated — requires live external services, and no CI pipeline exists yet (an existing, still-deferred non-goal).

## Open follow-ups (deferred to later sub-projects)

- Classification cascade (L1 rules, L3 LLM, L2 embeddings) — introduces LangChain/LangGraph.
- Policy layer (information barriers, alert budget, cost matrix) — needs `org_roles`, `buckets`, `rules`.
- Outbound delivery via `behavior_prompt()`.
- Audit trail (`routing_decisions`) and feedback loop (`overrides`).
- Dashboard API (role switcher, triage queue, override control).
- Cross-channel identity resolution (explicit stretch goal in the spec, not on the critical path).
