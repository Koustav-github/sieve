# Sieve Backend — Fine-Grained Classification Cascade — Design

Date: 2026-08-04

## Context

Ingestion (spec §4 stages 1–3) is done: every inbound message is persisted, deduped, attributed to a sender (`person_entities`/`channel_handles`), and stamped with a coarse bucket derived from arrival identity/connection (`careers`/`support`/`internal` — see `app/ingest/handler.py` and `app/ingest/identities.py`). Nothing past that exists yet.

This sub-project adds spec §4 stage 4 ("fine bucket — classification cascade"), scoped down from the spec's original 3-layer L1/owner-rules + L2/embeddings-shadow-mode + L3/LLM design to a simpler 2-layer cascade, per project-owner decision:

- **Layer 1** — keyword matching, fuzzy matching, and semantic similarity, evaluated together as one cheap layer (merges the spec's original L1 and L2 into one non-LLM pass).
- **Layer 2** — an LLM call (Grok), used only when Layer 1 produces no confident match.

This intentionally diverges from the spec document's L1/L2/L3 labels; "Layer 1"/"Layer 2" in this doc refer only to the 2-layer design above, not the spec's original three.

## Goals

- Classify every message into a **fine bucket** within its existing coarse bucket (careers/support/internal), using a hardcoded starter taxonomy.
- Layer 1: score each fine bucket in the message's coarse bucket via keyword match, fuzzy match, and semantic similarity; each signal has its own threshold; any one signal clearing its threshold for a bucket wins.
- Layer 2 (fallback): when no fine bucket wins on any Layer 1 signal, ask Grok to zero-shot classify against that coarse bucket's fine-bucket names/descriptions, with structured output (bucket, reason, confidence).
- Keep ingestion's `listen()` loop responsive: classification runs as an async follow-up after the message is already persisted and committed, not inline in `on_message`.
- Record which layer decided and with what confidence, on the message itself.

## Non-goals (deferred)

- Owner-editable buckets / dashboard UI for managing taxonomy — buckets are hardcoded in this sub-project; user-editable buckets are a later sub-project.
- Sender allowlists as a classification signal — considered during design and explicitly dropped; coarse bucket remains purely arrival-identity-derived, unchanged from ingestion.
- Subject extraction, visibility/policy engine, urgency/budget arbitration, outbound delivery, full audit trail (`routing_decisions`), feedback loop (`overrides`) — all still out of scope, per spec §4 stages 5–9.
- Retry/reprocessing of messages that fail classification (network error, timeout) — logged and left unclassified; an accepted risk at this stage, matching ingestion's existing failure posture.
- L2 shadow-mode embeddings-vs-LLM agreement reporting from the original spec — not applicable now that embeddings are folded into Layer 1 rather than run as a separate shadow layer.

## Architecture & components

```
apps/server/
├── app/
│   ├── classification/
│   │   ├── __init__.py
│   │   ├── buckets.py         # hardcoded taxonomy: fine buckets per coarse identity,
│   │   │                      # each with keywords + exemplar phrases + LLM description
│   │   ├── layer1.py          # keyword / fuzzy (rapidfuzz) / semantic (Pinecone) matching
│   │   ├── layer2.py          # Grok zero-shot call, structured output
│   │   ├── classifier.py      # orchestrates layer1 -> layer2 fallback, writes result
│   │   └── seed_pinecone.py   # one-off script: embeds each bucket's exemplars,
│   │                          # upserts one centroid vector per bucket into Pinecone
│   ├── ingest/
│   │   ├── worker.py          # creates the shared ThreadPoolExecutor, passes it to the handler
│   │   └── handler.py         # after persist+commit, submits classification to the executor
```

`app/ingest/worker.py` creates one `concurrent.futures.ThreadPoolExecutor` at startup and passes it into `build_on_message_handler`. The handler's job stays exactly as it is today through `db.commit()`; the only addition is `executor.submit(classifier.classify, message_id)` right after commit, non-blocking. This keeps `client.listen()`'s per-message dispatch loop fast — Pinecone/Grok network latency happens off that loop, in a worker thread.

## Data flow

1. Handler persists message, resolves sender, commits (unchanged from ingestion).
2. Handler submits `classifier.classify(message_id)` to the shared executor and returns.
3. In the executor thread, `classifier.classify`:
   a. Loads the message's coarse bucket (`agent_id`: careers/support/internal) and text.
   b. **Layer 1** (`layer1.py`), scoped to that coarse bucket's fine buckets only:
      - Keyword: exact/substring match against each bucket's keyword list.
      - Fuzzy: `rapidfuzz` similarity score against each bucket's keyword list.
      - Semantic: embed the message text via Pinecone's Inference API, query the Pinecone index (filtered to this coarse bucket's fine-bucket centroids) for cosine similarity.
      - Each signal has its own threshold. The first fine bucket where any signal clears its threshold wins. If multiple buckets would win, the earliest-declared bucket in `buckets.py` wins (deterministic tie-break).
   c. **Layer 2 fallback** (`layer2.py`): if no fine bucket won in Layer 1, call Grok with the coarse bucket's fine-bucket names + descriptions, requesting structured output `{bucket, reason, confidence}`.
   d. Writes `fine_bucket`, `classified_by` (`"layer1"` / `"layer2"`), `confidence`, `classified_at` onto the `messages` row.
4. On any exception (Pinecone/Grok error, timeout, malformed response): log at ERROR with the message id, leave the message's classification columns `NULL`. No retry in this sub-project.

## Starter taxonomy (hardcoded in `buckets.py`)

- **careers**: `job-seeking`, `specific-opening`, `referral`, `recruiter-inbound`
- **support**: `customer-complaints`, `suggestions`, `review`
- **internal**: `welfare`, `work-life`, `employee-security`

Each fine bucket entry holds: a short LLM-facing description (for Layer 2's zero-shot prompt), a keyword list (for Layer 1 exact/fuzzy matching), and 3–5 exemplar phrases (embedded once by `seed_pinecone.py` and averaged into that bucket's centroid vector). Not owner-editable yet — changing the taxonomy means editing `buckets.py` and re-running `seed_pinecone.py`.

## Data model change

One Alembic migration adding four nullable columns to `messages`:

```
messages
  ...(existing columns, unchanged)...
  fine_bucket     text, nullable
  classified_by   text, nullable   -- "layer1" | "layer2"
  confidence      float, nullable  -- Layer 2 only; Layer 1 leaves this NULL (it's a threshold match, not a probability)
  classified_at   timestamp, nullable
```

No new table. `routing_decisions` (spec §8) remains deferred to the future audit-trail sub-project.

## Config/secrets

New settings on `app/core/config.py`'s `Settings`, following the existing pattern for `CASPIAN_API_KEY`/`TELEGRAM_BOT_TOKEN`:

- `GROK_API_KEY` — xAI Grok, Layer 2.
- `PINECONE_API_KEY` — Pinecone, Layer 1 semantic signal (both embeddings and vector storage/query).
- `PINECONE_INDEX` — the index name `seed_pinecone.py` upserts into and `layer1.py` queries.

Added to `.env.example` alongside the existing Caspian/bot-token entries.

## Error handling

- **Layer 1 signal failure** (e.g. Pinecone request fails): that signal is skipped for this message (treated as "did not clear threshold"), not a hard failure — the other signals and Layer 2 fallback still get a chance. Logged at WARNING.
- **Layer 2 (Grok) failure or malformed structured output**: caught, logged at ERROR with the message id, message left unclassified (`fine_bucket = NULL`).
- **Classification failure must never affect ingestion**: the executor submission happens after `db.commit()`, so a classification failure of any kind cannot cause a message to be lost or unpersisted — worst case, a message simply sits unclassified.

## Testing

- `layer1.py`: unit tests per signal (keyword hit, fuzzy near-miss above/below threshold, semantic similarity above/below threshold via a mocked Pinecone client) — no live network call.
- `layer2.py`: unit test with a mocked Grok client, asserting structured-output parsing and the failure/malformed-response path.
- `classifier.py`: unit tests covering layer1-wins, layer1-fails-falls-to-layer2, and both-fail-stays-unclassified paths, plus the tie-break rule when multiple buckets would win Layer 1.
- No live smoke test automated — matches ingestion's existing precedent (manual only, no CI pipeline yet).

## Open follow-ups (deferred to later sub-projects)

- Owner-editable buckets and dashboard UI for taxonomy management.
- Sender-based classification signals (explicitly considered and dropped in this design).
- Retry/reprocessing for messages that failed classification.
- Full audit trail (`routing_decisions`) capturing every classification decision with reasoning, once the policy/visibility layer needs it.
- Subject extraction, visibility set, urgency/budget arbitration, outbound delivery (spec §4 stages 5–9).
