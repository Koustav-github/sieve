import pytest
from sqlalchemy.exc import IntegrityError

from app.classify.graph import build_classification_graph
from app.classify.pipeline import run_classification
from app.classify.schemas import L3ClassificationResult, SubjectExtractionResult
from app.ingest.message_store import persist_message
from app.models.bucket import Bucket
from app.models.routing_decision import RoutingDecision


class _FakeLLM:
    def __init__(self, result):
        self.result = result

    def invoke(self, prompt):
        return self.result


def test_run_classification_persists_routing_decision(session_factory, db_session):
    bucket = Bucket(name="customer_support", description="support", is_active=True)
    db_session.add(bucket)
    db_session.commit()

    message = persist_message(
        db_session,
        caspian_message_id="msg-1",
        agent_id="support",
        channel="email",
        sender_handle="a@example.com",
        thread_id=None,
        raw_payload={},
    )
    db_session.commit()

    graph = build_classification_graph(
        session_factory,
        _FakeLLM(L3ClassificationResult(bucket_name="customer_support", reason="matches", confidence=0.7)),
        _FakeLLM(SubjectExtractionResult(subject_name=None, reason="n/a")),
    )

    decision = run_classification(
        graph,
        db_session,
        message=message,
        agent_identity="support",
        sender_handle="a@example.com",
        subject="help",
        text="please help",
    )

    assert decision.id is not None
    stored = db_session.query(RoutingDecision).filter_by(message_id=message.id).one()
    assert stored.bucket_id == bucket.id
    assert stored.deciding_layer == "L3"


def test_run_classification_one_decision_per_message_enforced(session_factory, db_session):
    bucket = Bucket(name="customer_support", description="support", is_active=True)
    db_session.add(bucket)
    db_session.commit()

    message = persist_message(
        db_session,
        caspian_message_id="msg-2",
        agent_id="support",
        channel="email",
        sender_handle="a@example.com",
        thread_id=None,
        raw_payload={},
    )
    db_session.commit()

    graph = build_classification_graph(
        session_factory,
        _FakeLLM(L3ClassificationResult(bucket_name="customer_support", reason="matches", confidence=0.7)),
        _FakeLLM(SubjectExtractionResult(subject_name=None, reason="n/a")),
    )

    run_classification(
        graph,
        db_session,
        message=message,
        agent_identity="support",
        sender_handle="a@example.com",
        subject="help",
        text="please help",
    )

    with pytest.raises(IntegrityError):
        run_classification(
            graph,
            db_session,
            message=message,
            agent_identity="support",
            sender_handle="a@example.com",
            subject="help again",
            text="please help again",
        )
