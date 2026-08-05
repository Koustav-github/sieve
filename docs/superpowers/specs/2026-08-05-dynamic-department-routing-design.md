# Sieve Backend — Dynamic Department Routing — Design

Date: 2026-08-05

## Context

The cross-platform relay sub-project (`docs/superpowers/specs/2026-08-05-cross-platform-relay-design.md`, implemented and merged locally as of this date) built relay-to-a-fixed-3-identities (`careers`/`support`/`internal`), each backed by a Caspian connection registered once at worker startup, with a single-shot LLM call extracting a relay target + employment-ID claim from one message.

Per `rough.md` (repo root), Koustav described a richer target shape that supersedes parts of v1:

1. The registered-identity set is open-ended — arbitrary company departments (finance, hr, customercare, development, management, ...), not a fixed 3, administered by adding a department (lead name, team name, default platform, lead email) rather than editing code.
2. The bot operates in two distinct scopes with different rules: **group chats** (a department's own allow-listed channel, `@bot`-triggered) skip employee-ID verification entirely — membership in the private company chat already proves employee status — and deliver into the *target* department's own group chat. **Personal 1:1 chat** with the bot treats every message as implicit (no `@bot` needed), requires employee-ID verification (unless the target is exempt, e.g. customer support), and delivers to the target's *lead's email* specifically. An unverified personal-chat sender gets asked for their ID as a follow-up turn if they didn't already include it.
3. `customercare` (or whichever department is marked exempt) skips verification in both scopes, same as v1's `support`.

This sub-project replaces v1's fixed-identity model with this dynamic, scope-aware one. It supersedes the "keep careers/support/internal" decision made during v1's own brainstorming — Koustav confirmed this explicitly when asked.

Two follow-up sub-projects, deferred out of this one:
- **Knowledge-base auto-answer**: before relaying, an agent should try to answer from a company-policy knowledge base and only relay on a miss. Not addressed here — this sub-project only changes *who* can be a relay target and *how* routing/dispatch works; it does not add any answering capability.
- **Allow-list ingestion** (previously its own deferred follow-up, see memory) is absorbed into this sub-project's group-chat scope — a department's `connection_id` *is* its allow-list entry.

## Goals

- Let an admin register a new department (team name, lead name, lead email, platform, and which channel on that platform is theirs) via an API call — usable right away, no worker restart. The first department on a given platform provisions that platform's shared Caspian connection; later departments on the same platform reuse it.
- Route every inbound message down one of two paths based on where it arrived: **group chat** (the connection matches a registered department) or **personal DM** (the bot's own 1:1 presence).
- Group-chat path: detect an explicit bot-directed relay request via one LLM call, resolve the extracted target against the live `departments` table (open vocabulary), skip ID verification, and deliver into the target department's own group chat. Unmatched/ambiguous target falls back to *the* exempt department — this assumes exactly one `departments` row has `requires_verification = False` at any time; if none is registered, reply that the team wasn't recognized instead of falling back to nothing. (If more than one exempt department ever exists, which one wins is undefined here — flag it as a data-integrity concern for the implementation plan, e.g. enforce it with a partial unique index, rather than picking silently.)
- Personal-DM path: treat every message as an implicit request. Extract target + query text + (if present) an employee ID in one LLM call. Gate non-exempt targets behind employment-ID verification (cached on `PersonEntity`, same as v1). If unverified and no ID was given, hold the query and ask for one as a follow-up turn; the next message from that sender is checked against the held query before anything else.
- Personal-DM dispatch always goes to the target's `lead_email`, sent via one shared, bot-owned relay-sender email connection (not a per-department email connection).
- Full symmetry: any registered department can be both a relay source (its group chat can ask other departments things) and a relay target (others can reach it), same as v1's 3 identities.

## Non-goals (deferred)

- Knowledge-base auto-answer (separate sub-project, per Context above).
- Any change to how the `employees` table itself gets seeded/administered (still assumed pre-populated, same as v1).
- Expiring/timing out a pending verification — same "wait indefinitely" philosophy as v1's reply correlation.
- Migrating v1's existing `careers`/`support`/`internal` identities into this model — whether they become the first 3 rows of `departments` or are retired is an open question for the implementation plan, not settled here.
- Removing v1's `app/relay/*` code — this sub-project extends/replaces its identity-resolution and dispatch logic, not its overall shape (LLM-detect → auth-gate → dispatch → correlate-reply).

## Architecture & components

```
apps/server/app/
├── departments/
│   ├── __init__.py
│   ├── models.py         # PlatformConnection, Department, PendingVerification ORM models
│   ├── registry.py        # get_department(team_name), list_departments(),
│   │                       #   resolve_target(extracted_text) -> Department | None,
│   │                       #   match_group_message(connection_id, channel_ref) -> Department | None
│   └── admin_api.py        # POST /admin/departments — writes the row, provisioning
│                            #   a new platform_connections row (live Caspian call) only
│                            #   if this platform has no connection yet
├── relay/
│   ├── ... (v1's schemas.py/llm.py/auth.py/dispatcher.py stay, extended)
│   ├── scope.py            # NEW: classify_scope(message) -> "group" | "personal"
│   ├── group_pipeline.py   # NEW: run_group_relay() — the group-chat path
│   └── personal_pipeline.py # NEW: run_personal_relay() — the personal-DM path,
│                             #   including the pending-verification hold/resume
```

`app/ingest/worker.py`/`handler.py` keep their overall shape (one `CommClient`, one handler, one `listen()` loop, async executor) but the handler's dispatch-to-pipeline step branches on `scope.classify_scope(message)` instead of always calling one `run_relay`. The `connection_identities`-style dict v1 built once at startup from `register_identities()` is replaced, for departments, by a live (short-cached) DB lookup — so a department registered mid-run is immediately routable.

**Connection model** (clarified during brainstorming): platforms like Slack/Discord are installed once per workspace, not once per department — so multiple departments on the same platform share one `platform_connections` row, and are distinguished by `departments.channel_ref` (their specific channel/conversation within that shared connection). Registering the *first* department on a platform provisions a new connection (`install_slack()`/`connect_discord()`/etc.); registering a subsequent department on an already-connected platform reuses the existing `platform_connections` row and just records the new department's `channel_ref`.

**Open, not yet live-verified** (same class of uncertainty v1's `dispatcher.py` already documented and handled by failing loud rather than guessing): (1) how a specific Slack/Discord *channel* maps to a stable, obtainable identifier at registration time (does Caspian expose a `conversation_id`/channel id per channel via `list_conversations()` before any message has been sent there, or does a conversation only get an id once a message actually flows through it?) — this determines exactly how `channel_ref` gets populated when an admin registers a department; (2) how Caspian's message payload identifies which channel/conversation an inbound group message belongs to — needed to match an inbound message against a department's `channel_ref` (`Message.conversation_id` is the leading candidate, confirmed to exist on the real SDK dataclass, but whether it's stable/pre-obtainable per (1) needs the same live check); (3) the right Caspian SDK call for "deliver this into department X's own group chat" as opposed to `initiate()`'s cold-start-a-conversation-with-a-recipient shape, which fits the email path but not an existing channel. All three need one live check against the sandbox before implementation locks in the exact calls.

## Data flow

**Group-chat path** (inbound message's connection + channel-within-connection matches some `departments` row's `platform_connection_id` + `channel_ref`):
1. One LLM call: is this message a bot-directed relay request, and if so, what target + what text? Not a request → ignore, nothing happens.
2. Is a request → resolve extracted target text against the live `departments` table. No match/ambiguous → falls back to the one department with `requires_verification = False` (see Goals for what happens if zero or more than one exists).
3. No ID check. Deliver into the target department's own group chat (mechanism TBD, see Architecture's open item).

**Personal-DM path** (inbound connection is the bot's own 1:1 presence, not a department's group chat):
1. First: does a `pending_verifications` row exist for this sender/channel? If yes, this message is the ID follow-up, not a new query — extract just the ID, verify against `employees`, and either resume the held query (delete the pending row, dispatch) or reply "still couldn't verify" and drop the pending row (they'd have to re-ask).
2. No pending row → one LLM call extracts target department, query text, and an employee ID if the sender front-loaded it.
3. Target is exempt (`requires_verification = False`) → dispatch immediately.
4. Target requires verification and sender's `PersonEntity.verified_employee` is already `True` → dispatch immediately.
5. Otherwise: ID was extracted in step 2 → verify immediately (single-shot, same UX as v1) and dispatch or fall back same as v1's invalid-ID path. No ID → reply asking for one, write a `pending_verifications` row (upsert — a new request from the same sender replaces any existing pending one), stop.
6. Dispatch = email to `lead_email`, sent via the one shared relay-sender connection. Reply correlation back to the original DM conversation follows the same `conversation_id`-based approach v1 already built and proved correct.

## Data model

```
platform_connections
  id                PK
  platform          UNIQUE, text (slack | discord | telegram | email)
  connection_id      text — the one shared Caspian connection for this platform
                      (e.g. one Slack workspace install serves every Slack department)

departments
  id                     PK
  team_name              UNIQUE, text
  lead_name              text
  lead_email             text
  platform_connection_id  FK -> platform_connections.id
  channel_ref             text — identifies THIS department's specific channel/
                           conversation within the shared platform connection
                           (e.g. a Slack channel/conversation id) — see Architecture's
                           open item on how this is obtained/stays stable
  requires_verification     bool, default True
  created_at

pending_verifications
  id                PK
  sender_handle      text
  channel            text
  UNIQUE(sender_handle, channel)
  target_department_id  FK -> departments.id
  message_text        text
  created_at
```

`person_entities.verified_employee` (v1) is reused unchanged. `employees` (v1) is reused unchanged.

## Error handling

- **Pending verification never completed**: no timeout, no expiry — same philosophy as v1's indefinite reply-wait.
- **A second query arrives while one is still pending verification for that sender**: newer request replaces the held one (upsert on the `UNIQUE(sender_handle, channel)` constraint) — one outstanding ask per person at a time.
- **Group-to-group delivery fails**: reply with an error into the *source* group chat, mirroring v1's dispatch-failure handling.
- **Admin registers the first department on a platform but the live Caspian `connect_*()`/`install_*()` call fails**: the endpoint must not leave a half-created row — roll back the DB write (both the new `platform_connections` row, if one was being created, and the `departments` row), return an error to the admin caller. Registering a department on an *already-connected* platform has no live Caspian call to fail — it's a pure DB write.
- **The two flagged live-SDK unknowns** (group-vs-DM detection, group delivery call): fail loud with a clear error rather than guess silently, exactly like v1's `dispatcher.py` documented uncertainty pattern — pin down with one live check before this ships.

## Testing

Same established pattern (SQLite in-memory, fakes at the LLM/Caspian boundary, no live calls in the automated suite). New surface to cover: `departments` live-lookup (a freshly-inserted row is immediately routable — proves no restart is needed), the group-vs-DM scope split, the pending-verification hold/resume/replace logic (including the upsert-replaces-older-pending case), the group-chat unmatched-target fallback, and the admin endpoint's rollback-on-Caspian-failure behavior.

## Open follow-ups (deferred)

- Knowledge-base auto-answer sub-project (see Context).
- Whether/how v1's `careers`/`support`/`internal` become `departments` rows or get retired.
- Seeding/administering the `employees` table (still out of scope, same as v1).
