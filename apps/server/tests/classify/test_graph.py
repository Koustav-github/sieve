from app.classify.graph import ClassificationState, build_classification_graph
from app.classify.schemas import L3ClassificationResult, SubjectExtractionResult
from app.models.bucket import Bucket
from app.models.person import PersonEntity
from app.models.rule import Rule


class _FakeLLM:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def invoke(self, prompt):
        self.calls.append(prompt)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _initial_state(**overrides) -> ClassificationState:
    state: ClassificationState = {
        "agent_identity": "support",
        "sender_handle": "a@example.com",
        "subject": None,
        "text": None,
        "bucket_id": None,
        "bucket_name": None,
        "deciding_layer": None,
        "confidence": None,
        "reason": None,
        "subject_raw_text": None,
        "subject_person_entity_id": None,
    }
    state.update(overrides)
    return state


def test_l1_match_short_circuits_l3(session_factory, db_session):
    bucket = Bucket(name="escalation", description="escalation", is_active=True)
    db_session.add(bucket)
    db_session.flush()
    db_session.add(
        Rule(bucket_id=bucket.id, rule_type="keyword", pattern="urgent", is_active=True)
    )
    db_session.commit()

    l3_llm = _FakeLLM(
        L3ClassificationResult(bucket_name="escalation", reason="unused", confidence=0.9)
    )
    subject_llm = _FakeLLM(SubjectExtractionResult(subject_name=None, reason="n/a"))
    graph = build_classification_graph(session_factory, l3_llm, subject_llm)

    final = graph.invoke(_initial_state(subject="URGENT: fix now"))

    assert final["deciding_layer"] == "L1"
    assert final["bucket_name"] == "escalation"
    assert l3_llm.calls == []


def test_l3_runs_on_l1_miss_and_resolves_bucket(session_factory, db_session):
    bucket = Bucket(name="customer_support", description="support", is_active=True)
    db_session.add(bucket)
    db_session.commit()

    l3_llm = _FakeLLM(
        L3ClassificationResult(bucket_name="customer_support", reason="matches support", confidence=0.8)
    )
    graph = build_classification_graph(
        session_factory, l3_llm, _FakeLLM(SubjectExtractionResult(subject_name=None, reason="n/a"))
    )

    final = graph.invoke(_initial_state(subject="Need help", text="my order is late"))

    assert final["deciding_layer"] == "L3"
    assert final["bucket_name"] == "customer_support"
    assert final["confidence"] == 0.8


def test_l3_failure_produces_null_bucket_not_a_crash(session_factory, db_session):
    graph = build_classification_graph(
        session_factory,
        _FakeLLM(RuntimeError("timeout")),
        _FakeLLM(SubjectExtractionResult(subject_name=None, reason="n/a")),
    )

    final = graph.invoke(_initial_state(subject="hi", text="hi"))

    assert final["bucket_id"] is None
    assert final["deciding_layer"] == "L3"
    assert "L3 classification failed" in final["reason"]


def test_non_internal_traffic_skips_subject_extraction(session_factory, db_session):
    bucket = Bucket(name="customer_support", description="support", is_active=True)
    db_session.add(bucket)
    db_session.commit()

    subject_llm = _FakeLLM(SubjectExtractionResult(subject_name="Someone", reason="n/a"))
    graph = build_classification_graph(
        session_factory,
        _FakeLLM(L3ClassificationResult(bucket_name="customer_support", reason="x", confidence=0.5)),
        subject_llm,
    )

    graph.invoke(_initial_state(agent_identity="support", subject="hi", text="hi"))

    assert subject_llm.calls == []


def test_internal_traffic_runs_subject_extraction_and_resolves_person(session_factory, db_session):
    db_session.add(PersonEntity(display_name="Jane Doe", is_provisional=False))
    bucket = Bucket(name="internal_routine", description="routine", is_active=True)
    db_session.add(bucket)
    db_session.commit()

    subject_llm = _FakeLLM(SubjectExtractionResult(subject_name="Jane Doe", reason="named directly"))
    graph = build_classification_graph(
        session_factory,
        _FakeLLM(L3ClassificationResult(bucket_name="internal_routine", reason="x", confidence=0.5)),
        subject_llm,
    )

    final = graph.invoke(
        _initial_state(agent_identity="internal", subject="re: Jane", text="Jane missed the deadline")
    )

    assert final["subject_person_entity_id"] is not None
    assert final["subject_raw_text"] is None


def test_internal_traffic_falls_back_to_raw_text_when_unresolved(session_factory, db_session):
    bucket = Bucket(name="internal_routine", description="routine", is_active=True)
    db_session.add(bucket)
    db_session.commit()

    subject_llm = _FakeLLM(SubjectExtractionResult(subject_name="Someone Unknown", reason="named"))
    graph = build_classification_graph(
        session_factory,
        _FakeLLM(L3ClassificationResult(bucket_name="internal_routine", reason="x", confidence=0.5)),
        subject_llm,
    )

    final = graph.invoke(
        _initial_state(agent_identity="internal", subject="re: someone", text="body")
    )

    assert final["subject_person_entity_id"] is None
    assert final["subject_raw_text"] == "Someone Unknown"
