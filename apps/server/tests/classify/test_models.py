import pytest
from sqlalchemy.exc import IntegrityError

from app.models.bucket import Bucket
from app.models.message import Message
from app.models.routing_decision import RoutingDecision
from app.models.rule import Rule


def test_bucket_name_must_be_unique(db_session):
    db_session.add(Bucket(name="support_general", description="General support", is_active=True))
    db_session.commit()

    db_session.add(Bucket(name="support_general", description="Duplicate", is_active=True))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_rule_links_to_bucket(db_session):
    bucket = Bucket(name="urgent", description="Urgent escalations", is_active=True)
    db_session.add(bucket)
    db_session.flush()

    rule = Rule(bucket_id=bucket.id, rule_type="keyword", pattern="URGENT", is_active=True)
    db_session.add(rule)
    db_session.commit()

    assert rule.bucket.name == "urgent"


def test_routing_decision_requires_unique_message(db_session):
    bucket = Bucket(name="routine", description="Routine traffic", is_active=True)
    message = Message(
        caspian_message_id="msg-1",
        agent_id="support",
        channel="email",
        sender_handle="a@example.com",
        raw_payload={},
    )
    db_session.add_all([bucket, message])
    db_session.flush()

    db_session.add(
        RoutingDecision(
            message_id=message.id,
            deciding_layer="L1",
            bucket_id=bucket.id,
            confidence=None,
            reason="matched keyword rule",
        )
    )
    db_session.commit()

    db_session.add(
        RoutingDecision(
            message_id=message.id,
            deciding_layer="L1",
            bucket_id=bucket.id,
            confidence=None,
            reason="duplicate decision for the same message",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_routing_decision_allows_null_bucket_on_classification_failure(db_session):
    message = Message(
        caspian_message_id="msg-2",
        agent_id="support",
        channel="email",
        sender_handle="b@example.com",
        raw_payload={},
    )
    db_session.add(message)
    db_session.flush()

    decision = RoutingDecision(
        message_id=message.id,
        deciding_layer="L3",
        bucket_id=None,
        confidence=None,
        reason="classification failed: LLM timeout",
    )
    db_session.add(decision)
    db_session.commit()

    assert decision.bucket_id is None
