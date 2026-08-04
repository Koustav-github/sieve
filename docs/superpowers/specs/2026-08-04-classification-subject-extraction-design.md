# Classification cascade + subject extraction — design

Sub-project 2. Builds on the ingestion + core data model sub-project (`docs/superpowers/specs/2026-08-04-ingestion-data-model-design.md`), which already handles spec §4 stages 1–3 (ingest, dedup, sender resolution, coarse bucket via `connection_id`).

## Scope

Covers spec §4 stages 4–5 (fine-bucket classification, subject extraction) and §5 (the L1/L3 classification cascade).

**Out of scope, deliberately:**
- **L2 embedding prototypes / shadow mode.** Spec §5 and §12 both call this additive and explicitly non-critical-path ("Treating L2 as critical path" is listed as a risk). Separate, later sub-project.
- **Policy layer** (barriers, visibility sets, alert budget — spec §4 stages 6–7, §6). Needs `org_roles` and reporting-chain data that don't exist yet.
- **Delivery and audit** (spec §4 stages 8–9).
- **Dashboard / rule-editing UI** (spec's day 13). Buckets and rules are authored via a seed file for this sub-project, not an API.

## Architecture

A LangGraph `StateGraph` with three nodes, invoked synchronously from the existing `handler.py`'s `on_message` callback, immediately after `persist_message()` commits:

```
        ┌─────┐   no match   ┌─────┐
 msg ─▶ │ L1  │──────────────▶│ L3  │
        └─────┘               └─────┘
           │ match                │
           ▼                      ▼
      (bucket decided)     (bucket decided)
                                   │
                        internal-agent traffic?
                                   │ yes
                                   ▼
                          ┌─────────────────┐
                          │ subject_extract │
                          └─────────────────┘
```

- **L1 node** — plain Python, no LLM call. Loads active `rules` rows and checks trigger phrases / sender allowlist / domain against the inbound message. Conditional edge: match → bucket decided, proceed to the internal-traffic check; no match → L3.
- **L3 node** — one LangChain structured-output call (Pydantic schema: `bucket`, `reason`, `confidence`) zero-shot against the owner's bucket names + descriptions.
- **subject_extract node** — reached only for `internal`-identity traffic (spec §4: "applied only to internal-agent traffic"). One structured-output call extracts a name/handle, then attempts resolution against `person_entities` via the same lookup `sender_resolution.py` already uses.

Each node's output accumulates into a single `routing_decisions` row, written once the graph reaches END, in the same DB session but committed separately from the message insert (see Error handling).

LangGraph is used here (rather than plain LangChain calls) even though the current branching is simple, because it's the natural home for the L2 shadow-mode node and the override feedback loop (spec §7) when those sub-projects land — this graph is the extension point, not a one-off.

## Data model (spec §8, next 3 of 8 tables)

```
buckets
  id                PK
  name              UNIQUE — used in L3's zero-shot prompt and as L1 rule targets
  description        — used in L3's zero-shot prompt
  is_active         bool

rules                       (L1 layer)
  id                PK
  bucket_id         FK -> buckets
  rule_type         enum: keyword | sender_allowlist | domain
  pattern           text — the trigger phrase / sender address / domain to match
  is_active         bool

routing_decisions           (spec §8: "immutable")
  id                        PK
  message_id                FK -> messages, UNIQUE (one decision per message)
  deciding_layer            enum: L1 | L3
  bucket_id                 FK -> buckets, nullable (null when classification failed - see Error handling)
  confidence                float, nullable (L1 is deterministic; also null on classification failure)
  reason                    text
  subject_person_entity_id  FK -> person_entities, nullable
  subject_raw_text          text, nullable — set when extraction ran but couldn't resolve an entity
  created_at                timestamp
```

**Deliberately deferred** to future migrations, added only when the sub-project that reads/writes them exists — matching the "no columns for hypothetical future use" discipline already applied on the ingestion branch:
- `buckets.exemplars` / `buckets.centroid_vector` — L2-only.
- `buckets.destination_role` — policy-layer.
- `routing_decisions.visibility_set` / `exclusions` / `delivery_channel` — policy-layer / delivery-layer.

`buckets` and `rules` are seeded from a checked-in YAML/JSON file, loaded once at worker startup (same process that will grow to also `validate_identity_coverage()`), not via an API.

## Data flow

1. After `persist_message()` + `db.commit()` in `handler.py`, build the LangGraph input state from the persisted `Message` row and the resolved `PersonEntity`.
2. Invoke the graph synchronously in the same `on_message` callback — this blocks on LLM latency (one call for L1-miss traffic, two for internal-agent traffic), which is acceptable at this scale and matches spec §3's "one process, one handler" framing.
3. Persist the resulting `routing_decisions` row in the same DB session, committed separately from the message insert, so a classification failure can never roll back a successfully-ingested message.

## Error handling

- **LLM call fails or times out** (L3 or subject-extraction): catch, log, and still write a `routing_decisions` row with `bucket_id=NULL`, `deciding_layer='L3'`, `confidence=NULL`, and a `reason` describing the failure — never drop the message. An unclassified message must still exist for a later layer (manual triage, retry, etc.) to act on. This mirrors `handler.py`'s existing philosophy of never letting one message's failure take down the worker.
- **Subject extraction runs but resolves to no known person_entity**: store `subject_raw_text`, leave `subject_person_entity_id` null. Not an error — mirrors how `sender_resolution.py` already treats unknown senders as provisional rather than failures.
- **Seed file is missing, empty, or malformed at startup**: fail startup loudly, the same pattern used for `validate_identity_coverage()` on the ingestion branch — never run with zero L1 rules silently.

## Testing

- **L1**: pure Python against a fixture-seeded `rules` table; no mocking needed.
- **L3 / subject-extraction**: a fake LangChain chat model returning canned structured output (same pattern as `_FakeClient` in `test_identities.py`) — no real API calls in the unit suite.
- **Graph wiring**: verify an L1 match short-circuits L3, non-internal traffic skips subject-extraction, and a simulated LLM failure still produces a `routing_decisions` row instead of crashing the handler.
- Whether to add one live smoke test against the real Claude API (mirroring the ingestion sub-project's live Caspian test) is left to the implementation plan, not decided here.
