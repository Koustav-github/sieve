from app.ingest.sender_resolution import resolve_sender


def test_creates_provisional_person_on_unknown_handle(db_session):
    person = resolve_sender(db_session, channel="email", handle="new@example.com")
    db_session.commit()

    assert person.is_provisional is True
    assert person.id is not None


def test_returns_existing_person_on_known_handle(db_session):
    first = resolve_sender(db_session, channel="email", handle="known@example.com")
    db_session.commit()

    second = resolve_sender(db_session, channel="email", handle="known@example.com")

    assert second.id == first.id
