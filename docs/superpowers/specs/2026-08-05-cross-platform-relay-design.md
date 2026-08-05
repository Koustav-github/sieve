# Sieve Backend — Cross-Platform Relay — Design

Date: 2026-08-05

## Context

Sieve's classification-cascade sub-project (L1 rules, semantic/L1 signals, L3 LLM fallback, subject extraction — `app/classify`, plus the earlier `app/classification`) just merged into `main`. Per `changedPlan.md` (repo root), Koustav has changed Sieve's core direction: the actual value of `caspian-sdk` is cross-platform message relay (Telegram ↔ email ↔ Slack ↔ Discord), not classifying inbound messages into buckets. Bucketing, the L1/L3 cascade, and subject extraction are now redundant.

The new model: the 3 existing registered identities (`careers`, `support`, `internal` — unchanged from the ingestion-data-model sub-project) remain the fixed set of relay targets. Anyone, on any platform, can ask a bot to relay a message to one of these identities. `support` (customer support) accepts requests from anyone, unauthenticated. `careers` and `internal` require the sender to verify as an employee (via an employment ID checked against a new `employees` table) before a request can be relayed there; an unverified or failed claim falls back to relaying the request to `support` instead, with an explanation sent back to the sender. Once relayed, the target identity's reply is delivered back into the original requester's thread — one relay, one reply, no ongoing back-and-forth, no timeout (the system waits indefinitely for a reply).

This sub-project replaces the classification cascade in the ingest pipeline. It reuses the ingestion scaffolding built in `2026-08-04-ingestion-data-model-design.md` (one `CommClient`, one `on_message` handler, one `listen()` loop, the off-loop async executor pattern) — only the "what happens after a message is persisted" stage changes.

## Goals

- Detect, via one LLM call, whether an inbound message is an explicit relay request (e.g. "@bot let internal know we need sign-off on X"), and if so extract: target identity, the message text to relay, whether the sender claims to be an employee, and any employment ID they supplied.
- Gate relay to `careers`/`internal` behind employment-ID verification against a new `employees` table; cache verified status on the sender's `PersonEntity` so it isn't re-asked on every future request. `support` is always open, no verification.
- Dispatch the relay to the target identity's own already-registered address (the connection Caspian returned when that identity registered — no new contact directory needed), and correlate the eventual reply back to the original requester's thread via the Caspian `conversation_id` the relay was sent into.
- Remove the now-redundant classification cascade (`app/classify`, `app/classification`, `Bucket`, `Rule`, `RoutingDecision`, and `Message`'s unused `fine_bucket`/`classified_by`/`confidence`/`classified_at` columns — confirmed dead, never read or written anywhere outside their own definitions) and fix the `app/ingest/worker.py` wiring bug this uncovered (see Testing).

## Non-goals (deferred)

- Ongoing multi-turn relay conversations (back-and-forth) — one relay, one reply only.
- Reply timeouts / "no reply yet" notifications — the system waits indefinitely.
- Seeding or administering the `employees` table (assumed populated by the time this runs) — how employee records get created/managed is a separate, later concern.
- A general contact directory for identities — relay dispatch reuses each identity's own existing registered connection address.
- Any change to identity registration itself (`app/ingest/identities.py` is unchanged).

## Architecture & components

```
app/relay/
├── __init__.py
├── schemas.py     # RelayExtractionResult: is_relay_request, target_identity,
│                  #   message_text, claims_employee, employment_id
├── llm.py         # build_relay_llm() — one structured-output LLM call
├── auth.py        # verify_employment_id(db, employment_id) -> Employee | None
├── dispatcher.py  # send_relay(client, ...) wraps client.initiate();
│                  #   deliver_reply(client, ...) wraps client.reply()
└── pipeline.py    # run_relay(): reply-correlation check -> LLM detect ->
                   #   auth gate -> dispatch, mirrors app/classify/pipeline.py's
                   #   run_classification() shape
```

`app/ingest/worker.py` and `app/ingest/handler.py` keep their existing shape (one `CommClient`, one handler, one `listen()` loop, async executor off the loop) — only the classification-graph call is replaced by `run_relay()`. `app/classify/*`, `app/classification/*`, and the `Bucket`/`Rule`/`RoutingDecision` models are deleted; nothing outside those two packages depends on them.

## Data flow

1. **Reply-correlation check** (runs first, before any LLM call): every inbound message's `conversation_id` is checked against pending `relay_requests.target_conversation_id`. A match means this message *is* the target identity's reply — look up the source message via `relay_requests.source_message_id` and call `client.reply(source_message.caspian_message_id, text=...)` (the Caspian-assigned id, not our internal `messages.id`) to deliver it into the original requester's thread, mark the `relay_requests` row `completed`, done.
2. **No match → relay-detection LLM call.** One structured-output call returns `RelayExtractionResult{ is_relay_request, target_identity (careers|support|internal|None), message_text, claims_employee, employment_id }`.
3. **Not a relay request** → persist the message as today (dedup, sender resolution). No further action — bucketing is gone.
4. **Is a relay request** → resolve the sender's `PersonEntity` (already happens via `resolve_sender`), then gate on target:
   - `target_identity == "support"` → always allowed, dispatch immediately.
   - `target_identity in {"careers", "internal"}` and `PersonEntity.verified_employee` is already `True` → allowed, dispatch immediately.
   - Otherwise: if `claims_employee` and `employment_id` matches a row in `employees` → set `PersonEntity.verified_employee = True` (cached), dispatch to the requested target. Otherwise → `client.reply()` back to the sender explaining the ID couldn't be verified, **and** dispatch the original `message_text` to `support` instead (fallback, not a drop).
5. **Dispatch** always goes out over email — the one channel all 3 identities share (`IDENTITY_CHANNELS` in `identities.py`: careers/support/internal all register an email connection). Call `client.initiate(connection_id=<source identity's own email connection_id>, recipient=<target identity's own email address>, text=message_text)`, both already known from `register_identities()`'s return value — no new contact directory needed. Insert a `relay_requests` row (source message, source/target identity, the resulting `conversation_id`, `message_text`, `status=pending`).

## Data model

```
employees
  id                PK
  employment_id     UNIQUE, text — the ID a claimed employee provides
  name              text

relay_requests
  id                        PK
  source_message_id         FK -> messages.id
  source_identity            careers | support | internal
  target_identity            careers | support | internal
  target_conversation_id     text — Caspian conversation id the relay was sent into
  message_text               text — the extracted text that was relayed
  status                     pending | completed
  created_at / completed_at  timestamps

person_entities
  + verified_employee   bool, default False   (new column)
```

Removed: `buckets`, `rules`, `routing_decisions`, and `messages.fine_bucket`/`classified_by`/`confidence`/`classified_at` (dead columns — confirmed unused outside `app/models/message.py`'s own definition).

## Error handling

- **Relay-detection LLM call fails**: soft-fail like the old `l3_node`/`subject_extract_node` pattern — log, treat as "not a relay request," message still persists normally.
- **Employment-ID DB lookup fails** (DB error, not "no match"): fail closed — treat as unverified, same fallback path as an invalid ID.
- **Dispatch to the target identity fails**: log, reply to the original requester that the relay couldn't go through right now, create no `relay_requests` row (nothing was sent, nothing to correlate).
- **Delivering the lead's reply back fails**: log at ERROR, leave the `relay_requests` row `pending`. The reply text isn't lost — it's already persisted as a normal row in `messages` on the target's channel — it just won't auto-deliver back. Accepted v1 risk; no retry queue exists yet.
- **Inherited limitation, not new**: `identities.py` already documents that careers/support/internal's email connections can collapse onto one shared sandbox mailbox. Relay dispatch reuses those same registered addresses and inherits that risk as-is.

## Testing

SQLite in-memory DB, fake LLMs (`.invoke()` returning canned results), fake `CommClient` — same pattern already established in this codebase (`conftest.py`, `tests/ingest/test_worker.py`'s `_FakeClient`). No live Caspian/DB/API calls in the automated suite.

- `test_auth.py`: `verify_employment_id()` — valid id, unknown id, DB error path.
- `test_pipeline.py`: `run_relay()`, one test per branch from Data flow step 4 — reply-correlation match; non-relay message; `target=support` always allowed; already-verified employee skips re-asking; valid new claim+ID verifies and caches; invalid/missing ID replies + falls back to support; dispatch failure replies with an error and creates no `relay_requests` row.
- `test_dispatcher.py`: confirms `initiate()` and `reply()` are called with the right args for each case.
- **Fix `tests/ingest/test_worker.py`**: currently expects `build_l3_llm`/`build_subject_extraction_llm` (leftover from the classify system that never got wired into `worker.py` — this is also why `worker.main()` currently raises `TypeError: build_on_message_handler() missing 1 required positional argument: 'executor'` at runtime, confirmed by direct smoke test). Update it to expect the new relay LLM factory. Two tests in that file also have a pre-existing, unrelated `NameError` (missing `session_factory` fixture parameter) — fix those too while touching the file.
- Delete `tests/classify/*` along with the code it tests.

## Open follow-ups (deferred)

- Seeding/administering the `employees` table.
- Reply timeouts and "no reply yet" notifications.
- Multi-turn relay conversations.
- The allow-list ingestion filtering sub-project (previously scoped to sequence after classification-cascade — that dependency no longer applies since classification is being removed; still a separate, later sub-project).
