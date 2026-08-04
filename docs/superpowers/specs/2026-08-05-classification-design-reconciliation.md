# Classification cascade — design reconciliation

Two independent classification designs exist in this repo's history:

- `docs/superpowers/specs/2026-08-04-classification-subject-extraction-design.md` (this sub-project): LangGraph L1 (rules) → L3 (Anthropic zero-shot) → subject extraction, with a `routing_decisions` audit table (spec §8).
- `docs/superpowers/specs/2026-08-04-classification-cascade-design.md` (Koustav Manna, merged to `main` as docs only, no code): Layer 1 (keyword + fuzzy + Pinecone semantic) → Layer 2 (Groq zero-shot), async via a thread pool, four columns bolted onto `messages`, no subject extraction, no audit table.

This sub-project's implementation is what actually shipped (69 tests, twice-reviewed). This note records what was pulled in from the other design and what wasn't, and why.

## Kept from the other design

- **Async dispatch.** Classification (L1/L3/subject extraction) now runs on a `ThreadPoolExecutor`, submitted after the message is committed, instead of blocking the ingest `listen()` loop inline. See `app/ingest/handler.py`'s `_classify_and_record` and `app/ingest/worker.py`'s `CLASSIFICATION_EXECUTOR_WORKERS`. This was a genuine improvement over the original synchronous design — nothing about it conflicted with the L1/L3/subject-extraction/audit-table architecture, so it was a clean adoption.
- **Fuzzy keyword matching.** `app/classify/l1.py` now checks a `rapidfuzz` partial-ratio near-miss (threshold 90) when an exact substring match fails, catching typos/rewording without a new external service.
- **Semantic (Pinecone) matching**, as an *additional* L1 signal, not a replacement: `app/classify/semantic.py` + `app/classify/pinecone_client.py` + `app/classify/seed_pinecone.py`. Optional — skipped entirely if `PINECONE_API_KEY` is unset, so it never becomes a hard dependency. Decided as `L1_semantic` in `routing_decisions.deciding_layer`, distinct from a rule match (`L1`) or LLM fallback (`L3`).
- **Groq as an alternate L3 provider**, selected via `CLASSIFICATION_LLM_PROVIDER` (default `anthropic`, unchanged behavior). `app/classify/llm.py`'s `build_l3_llm(provider=...)`.

## Not kept, and why

- **No subject extraction in the other design.** Spec §4-5 requires it for internal-agent traffic; dropping it would be a regression against the spec, not a simplification. Subject extraction stays Anthropic-only — Groq was never validated for that node in either design.
- **No `routing_decisions` audit table in the other design** (columns bolted onto `messages` instead). Spec §8 explicitly calls for an immutable per-decision audit trail; the `messages`-column approach can't represent history (a later override/reclassification would overwrite the only record) or the subject-extraction fields. Kept `routing_decisions`.
- **Groq did not replace Anthropic outright.** Anthropic was already implemented, tested, and reviewed (including resolving a disputed reviewer claim that `temperature=0` breaks on Claude Sonnet — see `progress.md`); Groq is additive and off by default, not a wholesale vendor swap with no clear functional benefit.
