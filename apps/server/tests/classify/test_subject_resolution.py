from app.classify.subject_resolution import resolve_person_by_display_name
from app.models.person import PersonEntity


def test_resolves_existing_person_case_insensitively(db_session):
    db_session.add(PersonEntity(display_name="Jane Doe", is_provisional=False))
    db_session.commit()

    person = resolve_person_by_display_name(db_session, "jane doe")

    assert person is not None
    assert person.display_name == "Jane Doe"


def test_returns_none_when_no_match(db_session):
    person = resolve_person_by_display_name(db_session, "Nobody Here")

    assert person is None


def test_provisional_entity_with_no_display_name_is_never_matched(db_session):
    db_session.add(PersonEntity(display_name=None, is_provisional=True))
    db_session.commit()

    person = resolve_person_by_display_name(db_session, "Jane Doe")

    assert person is None


def test_multiple_matches_returns_one_without_raising(db_session):
    db_session.add(PersonEntity(display_name="John Smith", is_provisional=False))
    db_session.add(PersonEntity(display_name="john smith", is_provisional=False))
    db_session.commit()

    person = resolve_person_by_display_name(db_session, "John Smith")

    assert person is not None
    assert person.display_name.lower() == "john smith"
