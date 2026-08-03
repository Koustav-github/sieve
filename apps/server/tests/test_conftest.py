from app.models.person import PersonEntity


def test_db_session_fixture_creates_tables(db_session):
    person = PersonEntity(display_name="Test", is_provisional=False)
    db_session.add(person)
    db_session.commit()

    assert db_session.query(PersonEntity).count() == 1
