from sqlalchemy.orm import Session

from app.classify.graph import ClassificationState
from app.models.message import Message
from app.models.routing_decision import RoutingDecision


def build_initial_state(
    *, agent_identity: str, sender_handle: str, subject: str | None, text: str | None
) -> ClassificationState:
    return {
        "agent_identity": agent_identity,
        "sender_handle": sender_handle,
        "subject": subject,
        "text": text,
        "bucket_id": None,
        "bucket_name": None,
        "deciding_layer": None,
        "confidence": None,
        "reason": None,
        "subject_raw_text": None,
        "subject_person_entity_id": None,
    }


def run_classification(
    graph,
    db: Session,
    *,
    message: Message,
    agent_identity: str,
    sender_handle: str,
    subject: str | None,
    text: str | None,
) -> RoutingDecision:
    """Runs the compiled classification graph for one already-persisted
    `Message` and writes the resulting `RoutingDecision` row, committed in
    the given session as its own commit, separate from the message insert -
    a classification failure must never roll back a successfully-ingested
    message (see the design doc's error-handling section)."""
    state = build_initial_state(
        agent_identity=agent_identity, sender_handle=sender_handle, subject=subject, text=text
    )
    final_state = graph.invoke(state)

    decision = RoutingDecision(
        message_id=message.id,
        deciding_layer=final_state["deciding_layer"],
        bucket_id=final_state["bucket_id"],
        confidence=final_state["confidence"],
        reason=final_state["reason"],
        subject_person_entity_id=final_state["subject_person_entity_id"],
        subject_raw_text=final_state["subject_raw_text"],
    )
    db.add(decision)
    db.commit()
    return decision
