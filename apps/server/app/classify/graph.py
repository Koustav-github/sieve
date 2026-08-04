from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.classify.l1 import match_l1_rules
from app.classify.schemas import L3ClassificationResult, SubjectExtractionResult
from app.classify.subject_resolution import resolve_person_by_display_name
from app.models.bucket import Bucket


class ClassificationState(TypedDict):
    agent_identity: str
    sender_handle: str
    subject: str | None
    text: str | None
    bucket_id: int | None
    bucket_name: str | None
    deciding_layer: str | None
    confidence: float | None
    reason: str | None
    subject_raw_text: str | None
    subject_person_entity_id: int | None


def build_classification_graph(session_factory, l3_llm, subject_llm):
    """`session_factory` yields a fresh `Session` per node's DB access (same
    factory type as `build_on_message_handler`'s `session_factory`).
    `l3_llm`/`subject_llm` are Runnables (real: `app.classify.llm.build_l3_llm()`
    / `build_subject_extraction_llm()`; fakes in tests) whose
    `.invoke(prompt: str)` returns `L3ClassificationResult` /
    `SubjectExtractionResult`.

    Returns a compiled graph; call `.invoke(state)` with a `ClassificationState`
    (all decision fields `None`) to run it.
    """

    def l1_node(state: ClassificationState) -> dict:
        db = session_factory()
        try:
            match = match_l1_rules(
                db,
                sender_handle=state["sender_handle"],
                subject=state["subject"],
                text=state["text"],
            )
        finally:
            db.close()
        if match is None:
            return {}
        return {
            "bucket_id": match.bucket_id,
            "bucket_name": match.bucket_name,
            "deciding_layer": "L1",
            "confidence": None,
            "reason": match.reason,
        }

    def route_after_l1(state: ClassificationState) -> str:
        return "subject_gate" if state.get("bucket_id") is not None else "l3"

    def l3_node(state: ClassificationState) -> dict:
        db = session_factory()
        try:
            buckets = (
                db.query(Bucket).filter(Bucket.is_active.is_(True)).order_by(Bucket.id).all()
            )
            bucket_catalog = "\n".join(f"- {b.name}: {b.description}" for b in buckets)
            prompt = (
                "Classify this message into exactly one of the buckets below.\n\n"
                f"Buckets:\n{bucket_catalog}\n\n"
                f"Subject: {state['subject'] or '(none)'}\n"
                f"Body: {state['text'] or '(none)'}"
            )
            try:
                result: L3ClassificationResult = l3_llm.invoke(prompt)
            except Exception as exc:
                return {
                    "bucket_id": None,
                    "bucket_name": None,
                    "deciding_layer": "L3",
                    "confidence": None,
                    "reason": f"L3 classification failed: {exc}",
                }
            bucket = db.query(Bucket).filter(Bucket.name == result.bucket_name).one_or_none()
        finally:
            db.close()

        if bucket is None:
            return {
                "bucket_id": None,
                "bucket_name": result.bucket_name,
                "deciding_layer": "L3",
                "confidence": result.confidence,
                "reason": f"L3 returned unknown bucket {result.bucket_name!r}: {result.reason}",
            }
        return {
            "bucket_id": bucket.id,
            "bucket_name": bucket.name,
            "deciding_layer": "L3",
            "confidence": result.confidence,
            "reason": result.reason,
        }

    def route_after_classification(state: ClassificationState) -> str:
        return "subject_extract" if state["agent_identity"] == "internal" else END

    def subject_gate(state: ClassificationState) -> dict:
        return {}

    def subject_extract_node(state: ClassificationState) -> dict:
        prompt = (
            "Who is this internal message about, if anyone in particular? "
            "Answer with the person's name if the message names or clearly "
            "identifies one specific person as its subject; otherwise say none.\n\n"
            f"Subject: {state['subject'] or '(none)'}\n"
            f"Body: {state['text'] or '(none)'}"
        )
        try:
            result: SubjectExtractionResult = subject_llm.invoke(prompt)
        except Exception as exc:
            return {"subject_raw_text": f"subject extraction failed: {exc}"}

        if not result.subject_name:
            return {}

        db = session_factory()
        try:
            person = resolve_person_by_display_name(db, result.subject_name)
        finally:
            db.close()
        if person is not None:
            return {"subject_person_entity_id": person.id}
        return {"subject_raw_text": result.subject_name}

    graph = StateGraph(ClassificationState)
    graph.add_node("l1", l1_node)
    graph.add_node("l3", l3_node)
    graph.add_node("subject_gate", subject_gate)
    graph.add_node("subject_extract", subject_extract_node)

    graph.set_entry_point("l1")
    graph.add_conditional_edges(
        "l1", route_after_l1, {"subject_gate": "subject_gate", "l3": "l3"}
    )
    graph.add_conditional_edges(
        "l3", route_after_classification, {"subject_extract": "subject_extract", END: END}
    )
    graph.add_conditional_edges(
        "subject_gate",
        route_after_classification,
        {"subject_extract": "subject_extract", END: END},
    )
    graph.add_edge("subject_extract", END)

    return graph.compile()
