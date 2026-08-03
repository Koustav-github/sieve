from app.ingest.message_store import is_duplicate, persist_message


def test_new_message_is_not_duplicate(db_session):
    assert is_duplicate(db_session, "msg-1") is False


def test_persisted_message_is_duplicate(db_session):
    persist_message(
        db_session,
        caspian_message_id="msg-2",
        agent_id="support",
        channel="email",
        sender_handle="a@example.com",
        thread_id=None,
        raw_payload={"text": "hi"},
    )
    db_session.commit()

    assert is_duplicate(db_session, "msg-2") is True
