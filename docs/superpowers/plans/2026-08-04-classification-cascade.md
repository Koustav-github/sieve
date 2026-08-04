# Classification Cascade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify every persisted message into a fine-grained bucket (e.g. `job-seeking`, `customer-complaints`) within its existing coarse bucket, using a 2-layer cascade: Layer 1 (keyword + fuzzy + semantic-similarity matching) with a Layer 2 (Groq LLM) fallback.

**Architecture:** After `app/ingest/handler.py` persists and commits a message (unchanged), it submits the message's id to a shared `ThreadPoolExecutor` running in the same worker process. A background thread runs `app/classification/classifier.py`, which tries Layer 1 (`app/classification/layer1.py` — keyword/fuzzy against a hardcoded taxonomy, then semantic similarity via a Pinecone vector index) and falls back to Layer 2 (`app/classification/layer2.py` — a Groq zero-shot call) only if Layer 1 finds nothing. The result is written onto the `messages` row. Classification runs off `client.listen()`'s dispatch loop, so a slow/failed Pinecone or Groq call never delays ingestion of the next inbound message.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic, `rapidfuzz` (fuzzy string matching), `pinecone` (embeddings + vector store), `groq` (LLM fallback), pytest with the existing SQLite in-memory fixtures.

## Global Constraints

- Python `>=3.12` (`apps/server/pyproject.toml`).
- SQLAlchemy 2.0 declarative style only — `Mapped[...]` / `mapped_column(...)`, matching every existing model in `app/models/`.
- Dependencies are added via `uv add <package>` from `apps/server/`, matching how `caspian-sdk` was added (`pyproject.toml` `dependencies` list).
- No CI pipeline exists yet (an existing, deliberate non-goal per the ingestion spec) — all verification in this plan is `uv run pytest` / `uv run ruff check .`, run manually.
- Classification must never affect ingestion: a message is fully persisted and committed *before* classification is even submitted. Any classification failure (network error, malformed LLM output, missing Pinecone/Groq credentials) must be caught, logged, and leave the message simply unclassified — never raise into the caller.
- `GROQ_API_KEY` / `PINECONE_API_KEY` are not available yet (user will supply them later). Every task must work correctly with these blank — code must degrade gracefully (skip the affected signal/layer), never raise at import or startup time, matching the existing blank-`TELEGRAM_BOT_TOKEN`/`DISCORD_BOT_TOKEN` posture in `app/ingest/identities.py`.
- Tests never make real network calls to Pinecone or Groq — every test fakes/mocks the client objects.

---

## File Structure

```
apps/server/
├── app/
│   ├── classification/
│   │   ├── __init__.py
│   │   ├── buckets.py       # hardcoded taxonomy: FineBucket + FINE_BUCKETS
│   │   ├── clients.py       # build_pinecone_client() / build_groq_client()
│   │   ├── layer1.py        # keyword_or_fuzzy_match / semantic_match / match
│   │   ├── layer2.py        # Groq zero-shot classify()
│   │   ├── classifier.py    # classify(): orchestrates layer1 -> layer2, writes result
│   │   └── seed_pinecone.py # one-off script: embeds exemplars, upserts centroids
│   ├── core/config.py       # MODIFY: + groq_api_key, pinecone_api_key, pinecone_index
│   ├── models/message.py    # MODIFY: + fine_bucket, classified_by, confidence, classified_at
│   ├── ingest/
│   │   ├── handler.py       # MODIFY: submit_classification param, called after commit
│   │   └── worker.py        # MODIFY: build executor + clients, wire submit_classification
├── alembic/versions/         # NEW migration: add the 4 classification columns
├── tests/
│   ├── classification/
│   │   ├── __init__.py
│   │   ├── test_buckets.py
│   │   ├── test_clients.py
│   │   ├── test_layer1.py
│   │   ├── test_layer2.py
│   │   ├── test_classifier.py
│   │   └── test_seed_pinecone.py
│   └── ingest/
│       ├── test_handler.py  # MODIFY: + classification-dispatch tests
│       └── test_worker.py   # MODIFY: + classification-wiring test
.env.example                  # MODIFY: + GROQ_API_KEY, PINECONE_API_KEY, PINECONE_INDEX
docker-compose.yml             # MODIFY: ingest service + the 3 new env vars
```

---

### Task 1: Dependencies, settings, and config wiring

**Files:**
- Modify: `apps/server/pyproject.toml`
- Modify: `apps/server/app/core/config.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Test: `apps/server/tests/test_config.py` (new)

**Interfaces:**
- Produces: `settings.groq_api_key: str`, `settings.pinecone_api_key: str`, `settings.pinecone_index: str` — consumed by Task 5 (`clients.py`) and Task 4/9 (`layer1.py`/`seed_pinecone.py`, which read `settings.pinecone_index`).

- [ ] **Step 1: Add the three new dependencies**

Run from `apps/server/`:

```bash
uv add rapidfuzz groq pinecone
```

This updates `pyproject.toml`'s `dependencies` list and `uv.lock`.

- [ ] **Step 2: Write the failing test for new settings**

Create `apps/server/tests/test_config.py`:

```python
from app.core.config import Settings


def test_classification_settings_default_to_blank_and_named_index():
    s = Settings(_env_file=None)
    assert s.groq_api_key == ""
    assert s.pinecone_api_key == ""
    assert s.pinecone_index == "sieve-classification-buckets"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/test_config.py -v`
Expected: FAIL — `Settings` has no field `groq_api_key` (pydantic-settings raises or the attribute doesn't exist).

- [ ] **Step 3: Add the settings**

In `apps/server/app/core/config.py`, add three fields to `Settings` (after the existing `discord_bot_token` line):

```python
    groq_api_key: str = ""
    pinecone_api_key: str = ""
    pinecone_index: str = "sieve-classification-buckets"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server && uv run pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Add the new vars to `.env.example`**

Append to `.env.example` (after `DISCORD_BOT_TOKEN=`):

```
GROQ_API_KEY=
PINECONE_API_KEY=
PINECONE_INDEX=sieve-classification-buckets
```

- [ ] **Step 6: Wire the new vars into the `ingest` service in `docker-compose.yml`**

In the `ingest` service's `environment:` block, after `DISCORD_BOT_TOKEN: ${DISCORD_BOT_TOKEN}`, add:

```yaml
      GROQ_API_KEY: ${GROQ_API_KEY}
      PINECONE_API_KEY: ${PINECONE_API_KEY}
      PINECONE_INDEX: ${PINECONE_INDEX:-sieve-classification-buckets}
```

- [ ] **Step 7: Commit**

```bash
git add apps/server/pyproject.toml apps/server/uv.lock apps/server/app/core/config.py apps/server/tests/test_config.py .env.example docker-compose.yml
git commit -m "feat(classification): add rapidfuzz/groq/pinecone deps and settings"
```

---

### Task 2: Bucket taxonomy

**Files:**
- Create: `apps/server/app/classification/__init__.py` (empty)
- Create: `apps/server/app/classification/buckets.py`
- Test: `apps/server/tests/classification/__init__.py` (empty)
- Test: `apps/server/tests/classification/test_buckets.py`

**Interfaces:**
- Produces: `FineBucket` (dataclass: `name: str`, `description: str`, `keywords: list[str]`, `exemplars: list[str]`), `FINE_BUCKETS: dict[str, list[FineBucket]]` keyed by coarse bucket (`"careers"`/`"support"`/`"internal"`) — consumed by Task 3/4 (`layer1.py`), Task 6 (`layer2.py`), and Task 9 (`seed_pinecone.py`).

- [ ] **Step 1: Create the package directories**

```bash
mkdir -p apps/server/app/classification apps/server/tests/classification
touch apps/server/app/classification/__init__.py apps/server/tests/classification/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `apps/server/tests/classification/test_buckets.py`:

```python
from app.classification.buckets import FINE_BUCKETS


def test_all_coarse_identities_present():
    assert set(FINE_BUCKETS.keys()) == {"careers", "support", "internal"}


def test_every_fine_bucket_has_required_fields():
    for fine_buckets in FINE_BUCKETS.values():
        assert len(fine_buckets) > 0
        for bucket in fine_buckets:
            assert bucket.name
            assert bucket.description
            assert len(bucket.keywords) > 0
            assert len(bucket.exemplars) >= 3


def test_fine_bucket_names_are_unique_within_coarse_bucket():
    for fine_buckets in FINE_BUCKETS.values():
        names = [bucket.name for bucket in fine_buckets]
        assert len(names) == len(set(names))


def test_careers_taxonomy():
    assert [b.name for b in FINE_BUCKETS["careers"]] == [
        "job-seeking",
        "specific-opening",
        "referral",
        "recruiter-inbound",
    ]


def test_support_taxonomy():
    assert [b.name for b in FINE_BUCKETS["support"]] == [
        "customer-complaints",
        "suggestions",
        "review",
    ]


def test_internal_taxonomy():
    assert [b.name for b in FINE_BUCKETS["internal"]] == [
        "welfare",
        "work-life",
        "employee-security",
    ]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/classification/test_buckets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.classification.buckets'`

- [ ] **Step 4: Implement the taxonomy**

Create `apps/server/app/classification/buckets.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class FineBucket:
    name: str
    description: str
    keywords: list[str]
    exemplars: list[str]


FINE_BUCKETS: dict[str, list[FineBucket]] = {
    "careers": [
        FineBucket(
            name="job-seeking",
            description=(
                "Someone actively looking for a job opportunity at the company - a "
                "general inquiry about openings or an unsolicited application, not "
                "tied to one named role."
            ),
            keywords=[
                "job opening",
                "hiring",
                "position available",
                "job application",
                "vacancy",
                "job hunting",
            ],
            exemplars=[
                "Hi, I saw your company is hiring and I'd love to apply.",
                "Are there any open positions right now?",
                "I'm interested in joining your team, do you have any vacancies?",
                "Please find my resume attached for any suitable role.",
                "I'm currently job hunting and your company caught my eye.",
            ],
        ),
        FineBucket(
            name="specific-opening",
            description=(
                "References a specific, named job posting or requisition the "
                "company advertised, rather than a general inquiry."
            ),
            keywords=[
                "regarding the job posting",
                "job id",
                "req id",
                "posting for",
                "the role of",
            ],
            exemplars=[
                "I'm applying for the Senior Backend Engineer role posted on your careers page.",
                "This is regarding job ID SWE-2024-103.",
                "I saw the opening for a Product Designer and wanted to submit my application.",
                "Following up on the Data Analyst position listed on LinkedIn.",
                "I'd like to apply for the specific role of DevOps Engineer mentioned in your posting.",
            ],
        ),
        FineBucket(
            name="referral",
            description=(
                "Referring or recommending a candidate for a role, typically from "
                "an employee, friend, or acquaintance rather than the candidate "
                "themselves."
            ),
            keywords=[
                "referral",
                "referring",
                "would like to refer",
                "my friend works at",
                "referred by",
            ],
            exemplars=[
                "I'd like to refer a friend of mine for the open engineering role.",
                "My colleague is job hunting, can I refer her to your team?",
                "Referral: John Doe would be a great fit for your support position.",
                "I'm recommending a former teammate for your open position.",
                "Would love to refer someone I know for the marketing role.",
            ],
        ),
        FineBucket(
            name="recruiter-inbound",
            description=(
                "A recruiter, staffing agency, or headhunter reaching out on "
                "behalf of candidates or offering recruiting services."
            ),
            keywords=[
                "recruiter",
                "talent acquisition",
                "staffing agency",
                "headhunter",
                "on behalf of a candidate",
            ],
            exemplars=[
                "Hi, I'm a recruiter and I have a great candidate for your open role.",
                "I work with a staffing agency and wanted to discuss your hiring needs.",
                "As a headhunter, I specialize in placing engineers like the one you're looking for.",
                "I'd like to introduce you to a few pre-vetted candidates for your open positions.",
                "Reaching out from XYZ Recruiting on behalf of a strong candidate match.",
            ],
        ),
    ],
    "support": [
        FineBucket(
            name="customer-complaints",
            description=(
                "Expressing dissatisfaction or reporting a problem with a "
                "product, service, or order."
            ),
            keywords=[
                "complaint",
                "not working",
                "disappointed",
                "refund",
                "unacceptable",
                "broken",
            ],
            exemplars=[
                "I'm very disappointed with the product I received, it's broken.",
                "This is a complaint about the poor service I got yesterday.",
                "My order arrived damaged and I want a refund.",
                "I've had nothing but issues since I signed up.",
                "Unacceptable delay on my shipment, please fix this.",
            ],
        ),
        FineBucket(
            name="suggestions",
            description=(
                "Proposing an improvement, new feature, or idea rather than "
                "reporting a problem."
            ),
            keywords=[
                "suggestion",
                "it would be great if",
                "feature request",
                "you should add",
                "idea for improvement",
            ],
            exemplars=[
                "It would be great if you added dark mode to the app.",
                "Just a suggestion: maybe add a bulk export feature.",
                "I have an idea that could improve your onboarding flow.",
                "Feature request: support for multiple currencies.",
                "You should consider adding a mobile app for this.",
            ],
        ),
        FineBucket(
            name="review",
            description=(
                "General feedback or a review of the product/service, positive "
                "or neutral, not primarily a complaint or feature request."
            ),
            keywords=[
                "review",
                "rating",
                "five stars",
                "great experience",
                "highly recommend",
            ],
            exemplars=[
                "Just wanted to leave a review, great experience overall!",
                "Five stars, would recommend to anyone.",
                "Sharing my overall experience using your product for the past month.",
                "Great product, here's my honest review.",
                "Wanted to give feedback on my experience so far.",
            ],
        ),
    ],
    "internal": [
        FineBucket(
            name="welfare",
            description=(
                "Staff personal wellbeing, mental health, or a personal "
                "circumstance needing support."
            ),
            keywords=[
                "mental health",
                "wellbeing",
                "burnout",
                "leave of absence",
                "personal emergency",
            ],
            exemplars=[
                "I've been struggling with burnout lately and need to talk.",
                "Requesting a leave of absence for a personal emergency.",
                "I wanted to flag that I'm not doing well mentally right now.",
                "Can we discuss options for wellbeing support?",
                "I need some time off to deal with a family emergency.",
            ],
        ),
        FineBucket(
            name="work-life",
            description=(
                "Scheduling, remote work, time off, or balancing work and "
                "personal life."
            ),
            keywords=[
                "work from home",
                "flexible hours",
                "schedule change",
                "vacation request",
                "PTO",
            ],
            exemplars=[
                "Requesting to work from home next week.",
                "Can I shift my hours to start later in the day?",
                "Submitting my PTO request for next month.",
                "I'd like to discuss flexible working arrangements.",
                "Following up on my vacation request from last week.",
            ],
        ),
        FineBucket(
            name="employee-security",
            description=(
                "Reporting a safety, security, or harassment concern involving "
                "staff, requiring careful and confidential handling."
            ),
            keywords=[
                "harassment",
                "safety concern",
                "security incident",
                "report a concern",
                "confidential complaint",
            ],
            exemplars=[
                "I need to report a harassment incident involving a colleague.",
                "There's a safety concern in the office I want to flag.",
                "This is a confidential report about a security incident.",
                "I want to raise a concern about my safety at work.",
                "Reporting an incident that made me feel unsafe.",
            ],
        ),
    ],
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd apps/server && uv run pytest tests/classification/test_buckets.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add apps/server/app/classification/__init__.py apps/server/app/classification/buckets.py apps/server/tests/classification/__init__.py apps/server/tests/classification/test_buckets.py
git commit -m "feat(classification): add hardcoded fine-bucket taxonomy"
```

---

### Task 3: Layer 1 — keyword and fuzzy matching

**Files:**
- Create: `apps/server/app/classification/layer1.py`
- Test: `apps/server/tests/classification/test_layer1.py`

**Interfaces:**
- Consumes: `FINE_BUCKETS` from Task 2 (`app.classification.buckets`).
- Produces: `Layer1Match` (dataclass: `fine_bucket: str`, `signal: str`), `KEYWORD_SIGNAL = "keyword"`, `FUZZY_SIGNAL = "fuzzy"`, `FUZZY_THRESHOLD = 85.0`, `keyword_or_fuzzy_match(text: str, coarse_bucket: str) -> Layer1Match | None` — consumed by Task 4 (`match()` in this same file) and Task 8 (`classifier.py`, via `layer1.match`).

- [ ] **Step 1: Write the failing tests**

Create `apps/server/tests/classification/test_layer1.py`:

```python
from app.classification import layer1


def test_keyword_exact_match_wins():
    result = layer1.keyword_or_fuzzy_match(
        "I would like to apply for a job opening", "careers"
    )
    assert result == layer1.Layer1Match(fine_bucket="job-seeking", signal=layer1.KEYWORD_SIGNAL)


def test_fuzzy_match_wins_on_near_miss_keyword():
    # "resum" is a one-character-short near-miss of the "resume" keyword
    # under job-seeking; no exact substring match, but a high fuzzy ratio.
    result = layer1.keyword_or_fuzzy_match(
        "I've attached my resum for review", "careers"
    )
    assert result is not None
    assert result.fine_bucket == "job-seeking"
    assert result.signal == layer1.FUZZY_SIGNAL


def test_no_match_returns_none():
    result = layer1.keyword_or_fuzzy_match("The weather is nice today", "careers")
    assert result is None


def test_first_declared_bucket_wins_on_tie():
    # "referral" appears in both the referral bucket's keywords and,
    # coincidentally, nowhere else - this just confirms bucket order in
    # FINE_BUCKETS["careers"] is respected (referral is declared after
    # job-seeking/specific-opening, so an unambiguous referral keyword must
    # resolve to "referral", not an earlier bucket matching by accident).
    result = layer1.keyword_or_fuzzy_match(
        "I'd like to refer a friend for an open role", "careers"
    )
    assert result.fine_bucket == "referral"
```

Note: `resume` is a keyword on `job-seeking` per Task 2's taxonomy.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && uv run pytest tests/classification/test_layer1.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.classification.layer1'`

- [ ] **Step 3: Implement keyword/fuzzy matching**

Create `apps/server/app/classification/layer1.py`:

```python
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.classification.buckets import FINE_BUCKETS

FUZZY_THRESHOLD = 85.0

KEYWORD_SIGNAL = "keyword"
FUZZY_SIGNAL = "fuzzy"


@dataclass(frozen=True)
class Layer1Match:
    fine_bucket: str
    signal: str


def keyword_or_fuzzy_match(text: str, coarse_bucket: str) -> Layer1Match | None:
    lowered = text.lower()
    for bucket in FINE_BUCKETS[coarse_bucket]:
        for keyword in bucket.keywords:
            if keyword.lower() in lowered:
                return Layer1Match(fine_bucket=bucket.name, signal=KEYWORD_SIGNAL)
        best_fuzzy = max(
            (fuzz.partial_ratio(lowered, keyword.lower()) for keyword in bucket.keywords),
            default=0.0,
        )
        if best_fuzzy >= FUZZY_THRESHOLD:
            return Layer1Match(fine_bucket=bucket.name, signal=FUZZY_SIGNAL)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && uv run pytest tests/classification/test_layer1.py -v`
Expected: PASS (4 tests). If `test_fuzzy_match_wins_on_near_miss_keyword` fails because `rapidfuzz.fuzz.partial_ratio("i've attached my resum for review", "resume")` doesn't clear 85 in practice, print the actual score (`python -c "from rapidfuzz import fuzz; print(fuzz.partial_ratio('resum', 'resume'))"`) and adjust the test's input text (not the threshold) to a clearer near-miss until it clears 85 — the threshold itself is a deliberate design value from the approved spec.

- [ ] **Step 5: Commit**

```bash
git add apps/server/app/classification/layer1.py apps/server/tests/classification/test_layer1.py
git commit -m "feat(classification): add layer 1 keyword/fuzzy matching"
```

---

### Task 4: External clients (Pinecone / Groq builders)

**Files:**
- Create: `apps/server/app/classification/clients.py`
- Test: `apps/server/tests/classification/test_clients.py`

**Interfaces:**
- Consumes: `settings.pinecone_api_key`, `settings.groq_api_key`, `settings.pinecone_index` from Task 1 (`app.core.config`).
- Produces: `PINECONE_EMBED_MODEL = "multilingual-e5-large"`, `build_pinecone_client() -> Pinecone | None`, `build_groq_client() -> Groq | None` — consumed by Task 5 (`layer1.semantic_match`), Task 7 (`layer2.classify`, indirectly via worker wiring), Task 9 (`seed_pinecone.py`), and Task 11 (`worker.py`).

- [ ] **Step 1: Write the failing tests**

Create `apps/server/tests/classification/test_clients.py`:

```python
from unittest.mock import MagicMock

from app.classification import clients


def test_build_pinecone_client_returns_none_when_key_blank(monkeypatch):
    monkeypatch.setattr(clients.settings, "pinecone_api_key", "")
    assert clients.build_pinecone_client() is None


def test_build_groq_client_returns_none_when_key_blank(monkeypatch):
    monkeypatch.setattr(clients.settings, "groq_api_key", "")
    assert clients.build_groq_client() is None


def test_build_pinecone_client_constructs_with_key_when_present(monkeypatch):
    monkeypatch.setattr(clients.settings, "pinecone_api_key", "fake-key")
    fake_instance = MagicMock()
    fake_cls = MagicMock(return_value=fake_instance)
    monkeypatch.setattr(clients, "Pinecone", fake_cls)

    result = clients.build_pinecone_client()

    fake_cls.assert_called_once_with(api_key="fake-key")
    assert result is fake_instance


def test_build_groq_client_constructs_with_key_when_present(monkeypatch):
    monkeypatch.setattr(clients.settings, "groq_api_key", "fake-key")
    fake_instance = MagicMock()
    fake_cls = MagicMock(return_value=fake_instance)
    monkeypatch.setattr(clients, "Groq", fake_cls)

    result = clients.build_groq_client()

    fake_cls.assert_called_once_with(api_key="fake-key")
    assert result is fake_instance
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && uv run pytest tests/classification/test_clients.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.classification.clients'`

- [ ] **Step 3: Implement the client builders**

Create `apps/server/app/classification/clients.py`:

```python
import logging

from groq import Groq
from pinecone import Pinecone

from app.core.config import settings

logger = logging.getLogger(__name__)

PINECONE_EMBED_MODEL = "multilingual-e5-large"


def build_pinecone_client() -> Pinecone | None:
    if not settings.pinecone_api_key:
        logger.warning(
            "PINECONE_API_KEY is blank; semantic-similarity classification "
            "signal disabled"
        )
        return None
    return Pinecone(api_key=settings.pinecone_api_key)


def build_groq_client() -> Groq | None:
    if not settings.groq_api_key:
        logger.warning(
            "GROQ_API_KEY is blank; Layer 2 LLM classification fallback disabled"
        )
        return None
    return Groq(api_key=settings.groq_api_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && uv run pytest tests/classification/test_clients.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/server/app/classification/clients.py apps/server/tests/classification/test_clients.py
git commit -m "feat(classification): add Pinecone/Groq client builders with blank-key fallback"
```

---

### Task 5: Layer 1 — semantic similarity and combined match()

**Files:**
- Modify: `apps/server/app/classification/layer1.py`
- Modify: `apps/server/tests/classification/test_layer1.py`

**Interfaces:**
- Consumes: `PINECONE_EMBED_MODEL` from Task 4 (`app.classification.clients`), `settings.pinecone_index` from Task 1 (`app.core.config`).
- Produces: `SEMANTIC_SIGNAL = "semantic"`, `SEMANTIC_THRESHOLD = 0.75`, `semantic_match(text: str, coarse_bucket: str, pinecone_client: Any | None) -> Layer1Match | None`, `match(text: str, coarse_bucket: str, pinecone_client: Any | None) -> Layer1Match | None` — `match()` is consumed by Task 8 (`classifier.py`).

- [ ] **Step 1: Write the failing tests**

Append to `apps/server/tests/classification/test_layer1.py`:

```python
from unittest.mock import MagicMock


def test_semantic_match_returns_none_when_client_is_none():
    assert layer1.semantic_match("some text", "careers", None) is None


def test_semantic_match_returns_bucket_when_score_clears_threshold():
    fake_index = MagicMock()
    fake_index.query.return_value = {
        "matches": [{"score": 0.9, "metadata": {"fine_bucket": "referral"}}]
    }
    fake_client = MagicMock()
    fake_client.inference.embed.return_value = [{"values": [0.1, 0.2]}]
    fake_client.Index.return_value = fake_index

    result = layer1.semantic_match("some unrelated text", "careers", fake_client)

    assert result == layer1.Layer1Match(fine_bucket="referral", signal=layer1.SEMANTIC_SIGNAL)


def test_semantic_match_returns_none_when_below_threshold():
    fake_index = MagicMock()
    fake_index.query.return_value = {
        "matches": [{"score": 0.5, "metadata": {"fine_bucket": "referral"}}]
    }
    fake_client = MagicMock()
    fake_client.inference.embed.return_value = [{"values": [0.1, 0.2]}]
    fake_client.Index.return_value = fake_index

    assert layer1.semantic_match("some text", "careers", fake_client) is None


def test_semantic_match_returns_none_on_exception():
    fake_client = MagicMock()
    fake_client.inference.embed.side_effect = RuntimeError("network error")

    assert layer1.semantic_match("some text", "careers", fake_client) is None


def test_match_prefers_keyword_over_semantic():
    fake_client = MagicMock()

    result = layer1.match("I would like to apply for a job opening", "careers", fake_client)

    assert result.signal == layer1.KEYWORD_SIGNAL
    fake_client.inference.embed.assert_not_called()


def test_match_falls_back_to_semantic_when_no_keyword_fuzzy_hit():
    fake_index = MagicMock()
    fake_index.query.return_value = {
        "matches": [{"score": 0.9, "metadata": {"fine_bucket": "referral"}}]
    }
    fake_client = MagicMock()
    fake_client.inference.embed.return_value = [{"values": [0.1, 0.2]}]
    fake_client.Index.return_value = fake_index

    result = layer1.match("completely unrelated text about weather", "careers", fake_client)

    assert result == layer1.Layer1Match(fine_bucket="referral", signal=layer1.SEMANTIC_SIGNAL)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && uv run pytest tests/classification/test_layer1.py -v`
Expected: FAIL — `AttributeError: module 'app.classification.layer1' has no attribute 'semantic_match'`

- [ ] **Step 3: Implement semantic matching and the combined entry point**

In `apps/server/app/classification/layer1.py`, replace the top of the file (everything before the `FUZZY_THRESHOLD` line) with:

```python
import logging
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from app.classification.buckets import FINE_BUCKETS
from app.classification.clients import PINECONE_EMBED_MODEL
from app.core.config import settings

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 85.0
SEMANTIC_THRESHOLD = 0.75

KEYWORD_SIGNAL = "keyword"
FUZZY_SIGNAL = "fuzzy"
SEMANTIC_SIGNAL = "semantic"
```

Leave `Layer1Match` and `keyword_or_fuzzy_match` (from Task 3) unchanged below that, and append the two new functions at the end of the file:

```python
def semantic_match(text: str, coarse_bucket: str, pinecone_client: Any | None) -> Layer1Match | None:
    if pinecone_client is None:
        return None
    try:
        embed_response = pinecone_client.inference.embed(
            model=PINECONE_EMBED_MODEL,
            inputs=[text],
            parameters={"input_type": "query", "truncate": "END"},
        )
        vector = embed_response[0]["values"]
        index = pinecone_client.Index(settings.pinecone_index)
        query_response = index.query(
            vector=vector,
            filter={"coarse_bucket": coarse_bucket},
            top_k=1,
            include_metadata=True,
        )
        matches = query_response["matches"]
    except Exception:
        logger.warning(
            "Semantic-similarity signal failed for coarse_bucket=%s", coarse_bucket, exc_info=True
        )
        return None

    if not matches:
        return None
    top = matches[0]
    if top["score"] >= SEMANTIC_THRESHOLD:
        return Layer1Match(fine_bucket=top["metadata"]["fine_bucket"], signal=SEMANTIC_SIGNAL)
    return None


def match(text: str, coarse_bucket: str, pinecone_client: Any | None) -> Layer1Match | None:
    result = keyword_or_fuzzy_match(text, coarse_bucket)
    if result is not None:
        return result
    return semantic_match(text, coarse_bucket, pinecone_client)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && uv run pytest tests/classification/test_layer1.py -v`
Expected: PASS (10 tests total)

- [ ] **Step 5: Commit**

```bash
git add apps/server/app/classification/layer1.py apps/server/tests/classification/test_layer1.py
git commit -m "feat(classification): add layer 1 semantic-similarity matching"
```

---

### Task 6: Layer 2 — Groq LLM fallback

**Files:**
- Create: `apps/server/app/classification/layer2.py`
- Test: `apps/server/tests/classification/test_layer2.py`

**Interfaces:**
- Consumes: `FINE_BUCKETS` from Task 2 (`app.classification.buckets`).
- Produces: `Layer2Result` (dataclass: `fine_bucket: str`, `reason: str`, `confidence: float`), `GROQ_MODEL = "llama-3.3-70b-versatile"`, `classify(groq_client: Any | None, text: str, coarse_bucket: str) -> Layer2Result | None` — consumed by Task 8 (`classifier.py`, via `layer2.classify`).

- [ ] **Step 1: Write the failing tests**

Create `apps/server/tests/classification/test_layer2.py`:

```python
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.classification import layer2


def _fake_groq_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def test_classify_returns_none_when_client_is_none():
    assert layer2.classify(None, "some text", "careers") is None


def test_classify_parses_valid_structured_output():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_groq_response(
        json.dumps({"bucket": "job-seeking", "reason": "asks about openings", "confidence": 0.87})
    )

    result = layer2.classify(fake_client, "Do you have any open roles?", "careers")

    assert result == layer2.Layer2Result(
        fine_bucket="job-seeking", reason="asks about openings", confidence=0.87
    )


def test_classify_returns_none_on_malformed_json():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_groq_response("not json")

    assert layer2.classify(fake_client, "text", "careers") is None


def test_classify_returns_none_on_unknown_bucket_name():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_groq_response(
        json.dumps({"bucket": "not-a-real-bucket", "reason": "x", "confidence": 0.5})
    )

    assert layer2.classify(fake_client, "text", "careers") is None


def test_classify_returns_none_on_client_exception():
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("network error")

    assert layer2.classify(fake_client, "text", "careers") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && uv run pytest tests/classification/test_layer2.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.classification.layer2'`

- [ ] **Step 3: Implement Layer 2**

Create `apps/server/app/classification/layer2.py`:

```python
import json
import logging
from dataclasses import dataclass
from typing import Any

from app.classification.buckets import FINE_BUCKETS

logger = logging.getLogger(__name__)

GROQ_MODEL = "llama-3.3-70b-versatile"


@dataclass(frozen=True)
class Layer2Result:
    fine_bucket: str
    reason: str
    confidence: float


def _build_prompt(text: str, coarse_bucket: str) -> str:
    bucket_lines = "\n".join(
        f"- {bucket.name}: {bucket.description}" for bucket in FINE_BUCKETS[coarse_bucket]
    )
    return (
        "Classify the following message into exactly one of these categories:\n"
        f"{bucket_lines}\n\n"
        f"Message:\n{text}\n\n"
        "Respond with JSON only, no other text: "
        '{"bucket": "<category-name>", "reason": "<short reason>", '
        '"confidence": <number between 0.0 and 1.0>}'
    )


def classify(groq_client: Any | None, text: str, coarse_bucket: str) -> Layer2Result | None:
    if groq_client is None:
        return None

    valid_names = {bucket.name for bucket in FINE_BUCKETS[coarse_bucket]}
    prompt = _build_prompt(text, coarse_bucket)

    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        bucket = data["bucket"]
        reason = data["reason"]
        confidence = float(data["confidence"])
    except Exception:
        logger.warning(
            "Layer 2 classification failed for coarse_bucket=%s", coarse_bucket, exc_info=True
        )
        return None

    if bucket not in valid_names:
        logger.error(
            "Layer 2 returned unknown bucket %r for coarse_bucket=%s", bucket, coarse_bucket
        )
        return None

    return Layer2Result(fine_bucket=bucket, reason=reason, confidence=confidence)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && uv run pytest tests/classification/test_layer2.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/server/app/classification/layer2.py apps/server/tests/classification/test_layer2.py
git commit -m "feat(classification): add layer 2 Groq LLM fallback classification"
```

---

### Task 7: Data model — classification columns on `messages`

**Files:**
- Modify: `apps/server/app/models/message.py`
- Create: `apps/server/alembic/versions/<generated>_add_classification_columns_to_messages.py`
- Test: `apps/server/tests/test_classification_columns.py` (new)

**Interfaces:**
- Produces: `Message.fine_bucket: str | None`, `Message.classified_by: str | None`, `Message.confidence: float | None`, `Message.classified_at: datetime | None` — consumed by Task 8 (`classifier.py`).

- [ ] **Step 1: Write the failing test**

Create `apps/server/tests/test_classification_columns.py`:

```python
from datetime import UTC, datetime

from app.models.message import Message


def test_message_round_trips_classification_columns(session_factory):
    db = session_factory()
    try:
        message = Message(
            caspian_message_id="msg-classify-1",
            agent_id="careers",
            channel="email",
            sender_handle="someone@example.com",
            thread_id=None,
            raw_payload={"text": "hello"},
            fine_bucket="job-seeking",
            classified_by="layer1",
            confidence=None,
            classified_at=datetime.now(UTC),
        )
        db.add(message)
        db.commit()
        db.refresh(message)

        assert message.fine_bucket == "job-seeking"
        assert message.classified_by == "layer1"
        assert message.confidence is None
        assert message.classified_at is not None
    finally:
        db.close()


def test_message_classification_columns_default_to_null(session_factory):
    db = session_factory()
    try:
        message = Message(
            caspian_message_id="msg-classify-2",
            agent_id="support",
            channel="email",
            sender_handle="someone@example.com",
            thread_id=None,
            raw_payload={"text": "hello"},
        )
        db.add(message)
        db.commit()
        db.refresh(message)

        assert message.fine_bucket is None
        assert message.classified_by is None
        assert message.confidence is None
        assert message.classified_at is None
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/test_classification_columns.py -v`
Expected: FAIL — `TypeError: 'fine_bucket' is an invalid keyword argument for Message`

- [ ] **Step 3: Add the columns to the model**

In `apps/server/app/models/message.py`, change the import line and add the four columns after `received_at`:

```python
from sqlalchemy import JSON, DateTime, Float, String, UniqueConstraint, func
```

```python
    fine_bucket: Mapped[str | None] = mapped_column(String, nullable=True)
    classified_by: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

(the file already has `from datetime import datetime` at the top, so `datetime | None` resolves directly — matches the existing `thread_id: Mapped[str | None]` style)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server && uv run pytest tests/test_classification_columns.py -v`
Expected: PASS (2 tests) — the SQLite in-memory fixture builds tables straight from the ORM model, so this passes without needing the Alembic migration yet.

- [ ] **Step 5: Generate the Alembic migration for Postgres**

Run from `apps/server/`:

```bash
uv run alembic revision --autogenerate -m "add classification columns to messages"
```

Open the generated file in `apps/server/alembic/versions/`. Confirm `upgrade()` contains four `op.add_column('messages', ...)` calls (for `fine_bucket`, `classified_by`, `confidence`, `classified_at`, all nullable) and `downgrade()` contains matching `op.drop_column(...)` calls. If autogenerate produced anything else (e.g. picked up unrelated diffs), trim the file down to just these four columns.

- [ ] **Step 6: Commit**

```bash
git add apps/server/app/models/message.py apps/server/alembic/versions/ apps/server/tests/test_classification_columns.py
git commit -m "feat(classification): add classification columns to messages table"
```

---

### Task 8: Classifier orchestration

**Files:**
- Create: `apps/server/app/classification/classifier.py`
- Test: `apps/server/tests/classification/test_classifier.py`

**Interfaces:**
- Consumes: `layer1.match` (Task 5), `layer2.classify` (Task 6), `Message` model with classification columns (Task 7).
- Produces: `classify(session_factory: Callable[[], Session], pinecone_client: Any | None, groq_client: Any | None, message_id: int) -> None` — consumed by Task 11 (`worker.py`).

- [ ] **Step 1: Write the failing tests**

Create `apps/server/tests/classification/test_classifier.py`:

```python
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.classification import classifier
from app.models.message import Message


def _fake_groq_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _insert_message(session_factory, *, agent_id: str, text: str) -> int:
    db = session_factory()
    try:
        message = Message(
            caspian_message_id=f"msg-{text[:10]}",
            agent_id=agent_id,
            channel="email",
            sender_handle="someone@example.com",
            thread_id=None,
            raw_payload={"text": text},
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return message.id
    finally:
        db.close()


def test_classify_writes_layer1_result_when_matched(session_factory):
    message_id = _insert_message(
        session_factory, agent_id="careers", text="I would like to apply for a job opening"
    )

    classifier.classify(session_factory, pinecone_client=None, groq_client=None, message_id=message_id)

    db = session_factory()
    try:
        message = db.get(Message, message_id)
        assert message.fine_bucket == "job-seeking"
        assert message.classified_by == "layer1"
        assert message.confidence is None
        assert message.classified_at is not None
    finally:
        db.close()


def test_classify_falls_back_to_layer2_when_layer1_has_no_match(session_factory):
    message_id = _insert_message(
        session_factory, agent_id="careers", text="completely unrelated text about weather"
    )
    fake_groq = MagicMock()
    fake_groq.chat.completions.create.return_value = _fake_groq_response(
        json.dumps({"bucket": "job-seeking", "reason": "x", "confidence": 0.6})
    )

    classifier.classify(session_factory, pinecone_client=None, groq_client=fake_groq, message_id=message_id)

    db = session_factory()
    try:
        message = db.get(Message, message_id)
        assert message.fine_bucket == "job-seeking"
        assert message.classified_by == "layer2"
        assert message.confidence == 0.6
    finally:
        db.close()


def test_classify_leaves_message_unclassified_when_both_layers_fail(session_factory):
    message_id = _insert_message(
        session_factory, agent_id="careers", text="completely unrelated text about weather"
    )

    classifier.classify(session_factory, pinecone_client=None, groq_client=None, message_id=message_id)

    db = session_factory()
    try:
        message = db.get(Message, message_id)
        assert message.fine_bucket is None
        assert message.classified_by is None
    finally:
        db.close()


def test_classify_handles_missing_message_gracefully(session_factory):
    # Must not raise even if the message row doesn't exist.
    classifier.classify(session_factory, pinecone_client=None, groq_client=None, message_id=999999)


def test_classify_does_not_raise_when_layer1_raises(session_factory, monkeypatch):
    message_id = _insert_message(
        session_factory, agent_id="careers", text="I would like to apply for a job opening"
    )

    def failing_match(*args, **kwargs):
        raise RuntimeError("layer1 exploded")

    monkeypatch.setattr("app.classification.layer1.match", failing_match)

    # Must not raise; falls through to layer2 (also None here), ends unclassified.
    classifier.classify(session_factory, pinecone_client=None, groq_client=None, message_id=message_id)

    db = session_factory()
    try:
        message = db.get(Message, message_id)
        assert message.fine_bucket is None
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && uv run pytest tests/classification/test_classifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.classification.classifier'`

- [ ] **Step 3: Implement the orchestrator**

Create `apps/server/app/classification/classifier.py`:

```python
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.classification import layer1, layer2
from app.models.message import Message

logger = logging.getLogger(__name__)


def classify(
    session_factory: Callable[[], Session],
    pinecone_client: Any | None,
    groq_client: Any | None,
    message_id: int,
) -> None:
    db = session_factory()
    try:
        message = db.get(Message, message_id)
        if message is None:
            logger.error("classify: message %s not found", message_id)
            return

        coarse_bucket = message.agent_id
        text = message.raw_payload.get("text") or ""

        layer1_match = _try_layer1(text, coarse_bucket, pinecone_client, message_id)
        if layer1_match is not None:
            message.fine_bucket = layer1_match.fine_bucket
            message.classified_by = "layer1"
            message.classified_at = datetime.now(UTC)
            db.commit()
            return

        layer2_result = _try_layer2(text, coarse_bucket, groq_client, message_id)
        if layer2_result is not None:
            message.fine_bucket = layer2_result.fine_bucket
            message.classified_by = "layer2"
            message.confidence = layer2_result.confidence
            message.classified_at = datetime.now(UTC)
            db.commit()
            return

        logger.error("Message %s could not be classified by either layer", message_id)
    except Exception:
        db.rollback()
        logger.exception("Unexpected error classifying message %s", message_id)
    finally:
        db.close()


def _try_layer1(
    text: str, coarse_bucket: str, pinecone_client: Any | None, message_id: int
) -> layer1.Layer1Match | None:
    try:
        return layer1.match(text, coarse_bucket, pinecone_client)
    except Exception:
        logger.exception("Layer 1 raised unexpectedly for message %s", message_id)
        return None


def _try_layer2(
    text: str, coarse_bucket: str, groq_client: Any | None, message_id: int
) -> layer2.Layer2Result | None:
    try:
        return layer2.classify(groq_client, text, coarse_bucket)
    except Exception:
        logger.exception("Layer 2 raised unexpectedly for message %s", message_id)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && uv run pytest tests/classification/test_classifier.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/server/app/classification/classifier.py apps/server/tests/classification/test_classifier.py
git commit -m "feat(classification): add classifier orchestration (layer1 -> layer2 fallback)"
```

---

### Task 9: Pinecone centroid seeding script

**Files:**
- Create: `apps/server/app/classification/seed_pinecone.py`
- Test: `apps/server/tests/classification/test_seed_pinecone.py`

**Interfaces:**
- Consumes: `FINE_BUCKETS` (Task 2), `PINECONE_EMBED_MODEL`, `build_pinecone_client` (Task 4), `settings.pinecone_index` (Task 1).
- Produces: `average_vector(vectors: list[list[float]]) -> list[float]`, `embed_exemplars(pinecone_client: Any, texts: list[str]) -> list[list[float]]`, `seed() -> None` — `seed()` is a manual entry point, not consumed by other tasks.

- [ ] **Step 1: Write the failing tests**

Create `apps/server/tests/classification/test_seed_pinecone.py`:

```python
from unittest.mock import MagicMock

from app.classification import seed_pinecone


def test_average_vector_computes_elementwise_mean():
    vectors = [[1.0, 2.0, 3.0], [3.0, 4.0, 5.0]]
    assert seed_pinecone.average_vector(vectors) == [2.0, 3.0, 4.0]


def test_embed_exemplars_calls_inference_embed_with_passage_input_type():
    fake_client = MagicMock()
    fake_client.inference.embed.return_value = [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]

    result = seed_pinecone.embed_exemplars(fake_client, ["hello", "world"])

    fake_client.inference.embed.assert_called_once_with(
        model=seed_pinecone.PINECONE_EMBED_MODEL,
        inputs=["hello", "world"],
        parameters={"input_type": "passage", "truncate": "END"},
    )
    assert result == [[0.1, 0.2], [0.3, 0.4]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && uv run pytest tests/classification/test_seed_pinecone.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.classification.seed_pinecone'`

- [ ] **Step 3: Implement the seeding script**

Create `apps/server/app/classification/seed_pinecone.py`:

```python
"""One-off script: embed each fine bucket's exemplar phrases via Pinecone's
Inference API, average them into one centroid per bucket, and upsert into
the configured Pinecone index.

Run manually after editing app/classification/buckets.py:
    uv run python -m app.classification.seed_pinecone
"""

import logging
from typing import Any

from app.classification.buckets import FINE_BUCKETS
from app.classification.clients import PINECONE_EMBED_MODEL, build_pinecone_client
from app.core.config import settings

logger = logging.getLogger(__name__)


def embed_exemplars(pinecone_client: Any, texts: list[str]) -> list[list[float]]:
    response = pinecone_client.inference.embed(
        model=PINECONE_EMBED_MODEL,
        inputs=texts,
        parameters={"input_type": "passage", "truncate": "END"},
    )
    return [item["values"] for item in response]


def average_vector(vectors: list[list[float]]) -> list[float]:
    length = len(vectors[0])
    return [sum(vector[i] for vector in vectors) / len(vectors) for i in range(length)]


def seed() -> None:
    pinecone_client = build_pinecone_client()
    if pinecone_client is None:
        raise RuntimeError("PINECONE_API_KEY must be set to run seeding")

    index = pinecone_client.Index(settings.pinecone_index)

    vectors_to_upsert = []
    for coarse_bucket, fine_buckets in FINE_BUCKETS.items():
        for bucket in fine_buckets:
            embeddings = embed_exemplars(pinecone_client, bucket.exemplars)
            centroid = average_vector(embeddings)
            vectors_to_upsert.append(
                {
                    "id": f"{coarse_bucket}:{bucket.name}",
                    "values": centroid,
                    "metadata": {"coarse_bucket": coarse_bucket, "fine_bucket": bucket.name},
                }
            )

    index.upsert(vectors=vectors_to_upsert)
    logger.info(
        "Seeded %d bucket centroids into Pinecone index %s",
        len(vectors_to_upsert),
        settings.pinecone_index,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && uv run pytest tests/classification/test_seed_pinecone.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/server/app/classification/seed_pinecone.py apps/server/tests/classification/test_seed_pinecone.py
git commit -m "feat(classification): add Pinecone bucket-centroid seeding script"
```

---

### Task 10: Wire classification into the ingestion handler

**Files:**
- Modify: `apps/server/app/ingest/handler.py`
- Modify: `apps/server/tests/ingest/test_handler.py`

**Interfaces:**
- Consumes: nothing new directly (accepts an opaque `submit_classification` callable — no import of `classifier.py` needed here, keeping the handler decoupled from classification internals).
- Produces: `build_on_message_handler(session_factory, connection_identities, submit_classification: Callable[[int], None] = lambda message_id: None)` — the new third parameter is consumed by Task 11 (`worker.py`).

- [ ] **Step 1: Write the failing tests**

Append to `apps/server/tests/ingest/test_handler.py`:

```python
def test_handler_submits_new_message_id_for_classification(session_factory):
    submitted: list[int] = []
    handle = build_on_message_handler(
        session_factory, CONNECTION_IDENTITIES, submit_classification=submitted.append
    )
    handle(_fake_message(id="msg-300"))

    db = session_factory()
    try:
        message = db.query(Message).filter_by(caspian_message_id="msg-300").one()
        assert submitted == [message.id]
    finally:
        db.close()


def test_handler_does_not_submit_classification_for_duplicate_message(session_factory):
    submitted: list[int] = []
    handle = build_on_message_handler(
        session_factory, CONNECTION_IDENTITIES, submit_classification=submitted.append
    )
    handle(_fake_message(id="msg-301"))
    handle(_fake_message(id="msg-301"))

    assert len(submitted) == 1


def test_handler_does_not_submit_classification_when_fields_missing(session_factory):
    submitted: list[int] = []
    handle = build_on_message_handler(
        session_factory, CONNECTION_IDENTITIES, submit_classification=submitted.append
    )
    handle(_fake_message(id="msg-302", sender={}))

    assert submitted == []


def test_handler_defaults_submit_classification_to_a_no_op(session_factory):
    # Existing callers (and other tests in this file) that don't pass
    # submit_classification must keep working unchanged.
    handle = build_on_message_handler(session_factory, CONNECTION_IDENTITIES)
    handle(_fake_message(id="msg-303"))  # must not raise

    db = session_factory()
    try:
        assert db.query(Message).filter_by(caspian_message_id="msg-303").count() == 1
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && uv run pytest tests/ingest/test_handler.py -v -k classification`
Expected: FAIL — `TypeError: build_on_message_handler() got an unexpected keyword argument 'submit_classification'`

- [ ] **Step 3: Wire the handler**

In `apps/server/app/ingest/handler.py`, change the function signature:

```python
def build_on_message_handler(
    session_factory: Callable[[], Session],
    connection_identities: dict[str, str],
    submit_classification: Callable[[int], None] = lambda message_id: None,
) -> Callable[[Any], None]:
```

Then, inside `handle()`, change the `persist_message(...)` call site so the returned row's id is captured and classification is submitted right after commit:

```python
            persisted = persist_message(
                db,
                caspian_message_id=message_id,
                agent_id=agent_id,
                channel=channel,
                sender_handle=sender_handle,
                thread_id=thread_id,
                raw_payload=_raw_payload(message),
            )
            persisted_pk = persisted.id
            db.commit()
            submit_classification(persisted_pk)
```

(this replaces the existing `persist_message(...)` call, which previously didn't capture a return value, and the bare `db.commit()` line right after it)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && uv run pytest tests/ingest/test_handler.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones — the default no-op keeps them working unchanged)

- [ ] **Step 5: Commit**

```bash
git add apps/server/app/ingest/handler.py apps/server/tests/ingest/test_handler.py
git commit -m "feat(classification): submit persisted messages for classification after commit"
```

---

### Task 11: Wire classification into the ingestion worker

**Files:**
- Modify: `apps/server/app/ingest/worker.py`
- Modify: `apps/server/tests/ingest/test_worker.py`

**Interfaces:**
- Consumes: `classifier.classify` (Task 8), `build_pinecone_client`/`build_groq_client` (Task 4), `build_on_message_handler`'s new `submit_classification` param (Task 10).
- Produces: nothing further downstream — this is the final wiring task.

- [ ] **Step 1: Write the failing test**

Append to `apps/server/tests/ingest/test_worker.py`:

```python
def test_main_wires_submit_classification_into_handler(monkeypatch):
    """Classification sub-project: main() must build the executor + Pinecone/Groq
    clients and pass a submit_classification callable into build_on_message_handler
    - even with PINECONE_API_KEY/GROQ_API_KEY blank, since both build_* helpers
    degrade gracefully to None rather than raising (see clients.py)."""
    fake_client = _FakeClient()
    monkeypatch.setattr(worker_module, "CommClient", lambda: fake_client)
    monkeypatch.setattr(
        worker_module,
        "register_identities",
        lambda client: {
            ("careers", "email"): {"id": "conn-careers-email"},
            ("support", "email"): {"id": "conn-support-email"},
            ("internal", "email"): {"id": "conn-internal-email"},
        },
    )

    captured = {}

    def fake_build_handler(session_factory, connection_identities, submit_classification):
        captured["submit_classification"] = submit_classification
        return lambda message: None

    monkeypatch.setattr(worker_module, "build_on_message_handler", fake_build_handler)

    worker_module.main()

    assert callable(captured["submit_classification"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/ingest/test_worker.py -v -k classification`
Expected: FAIL — `TypeError: fake_build_handler() missing 1 required positional argument: 'submit_classification'` (current `main()` still calls `build_on_message_handler` with only 2 args)

- [ ] **Step 3: Wire the worker**

In `apps/server/app/ingest/worker.py`, update the imports:

```python
import logging
from concurrent.futures import ThreadPoolExecutor

from caspian_sdk import CommClient

from app.classification.classifier import classify as classify_message
from app.classification.clients import build_groq_client, build_pinecone_client
from app.db.session import SessionLocal
from app.ingest.handler import build_on_message_handler
from app.ingest.identities import (
    connection_identity_map,
    register_identities,
    validate_identity_coverage,
)
```

Then, in `main()`, replace the block from `connection_identities = connection_identity_map(results)` through `client.on_message(build_on_message_handler(SessionLocal, connection_identities))` with:

```python
    connection_identities = connection_identity_map(results)
    logger.info("connection -> identity map: %r", connection_identities)
    validate_identity_coverage(results, connection_identities)

    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sieve-classify")
    pinecone_client = build_pinecone_client()
    groq_client = build_groq_client()

    def submit_classification(message_id: int) -> None:
        executor.submit(classify_message, SessionLocal, pinecone_client, groq_client, message_id)

    client.on_message(
        build_on_message_handler(SessionLocal, connection_identities, submit_classification)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && uv run pytest tests/ingest/test_worker.py -v`
Expected: PASS (all tests in the file, including the 4 pre-existing ones — `build_pinecone_client()`/`build_groq_client()` return `None` with blank settings, so no real network/credential requirement is introduced)

- [ ] **Step 5: Run the full backend test suite**

Run: `cd apps/server && uv run pytest -v`
Expected: PASS, all tests across `tests/classification/`, `tests/ingest/`, and the pre-existing `tests/test_health.py`/`tests/test_conftest.py`/`tests/test_config.py`/`tests/test_classification_columns.py`.

- [ ] **Step 6: Lint**

Run: `cd apps/server && uv run ruff check .`
Expected: no errors (fix any import-order/unused-import issues ruff flags before committing)

- [ ] **Step 7: Commit**

```bash
git add apps/server/app/ingest/worker.py apps/server/tests/ingest/test_worker.py
git commit -m "feat(classification): wire classification executor + clients into the ingestion worker"
```

---

## Post-implementation notes for the user

- Live classification won't actually run until real `GROQ_API_KEY`/`PINECONE_API_KEY` values are added to your root `.env` — until then, `build_pinecone_client()`/`build_groq_client()` return `None`, Layer 1's semantic signal and all of Layer 2 are silently skipped, and messages simply stay unclassified (keyword/fuzzy matching in Layer 1 still works with no keys, since it needs neither).
- Once you have a Pinecone index created and `PINECONE_API_KEY`/`PINECONE_INDEX` set, run `uv run python -m app.classification.seed_pinecone` once (from `apps/server/`) to populate the bucket centroids before semantic matching can return results.
- The Postgres migration from Task 7 needs `docker compose up` (or `alembic upgrade head` directly) to actually apply to your dev database — the `migrate` service in `docker-compose.yml` already runs this automatically on every `docker compose up`.
