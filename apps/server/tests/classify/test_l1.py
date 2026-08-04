from app.classify.l1 import match_l1_rules
from app.models.bucket import Bucket
from app.models.rule import Rule


def _make_bucket_with_rule(db_session, *, bucket_name, rule_type, pattern):
    bucket = Bucket(name=bucket_name, description=bucket_name, is_active=True)
    db_session.add(bucket)
    db_session.flush()
    db_session.add(
        Rule(bucket_id=bucket.id, rule_type=rule_type, pattern=pattern, is_active=True)
    )
    db_session.commit()
    return bucket


def test_keyword_rule_matches_case_insensitively_in_subject(db_session):
    _make_bucket_with_rule(
        db_session, bucket_name="escalation", rule_type="keyword", pattern="urgent"
    )

    result = match_l1_rules(
        db_session, sender_handle="a@example.com", subject="URGENT: server down", text=None
    )

    assert result is not None
    assert result.bucket_name == "escalation"


def test_keyword_rule_matches_in_text_not_just_subject(db_session):
    _make_bucket_with_rule(
        db_session, bucket_name="escalation", rule_type="keyword", pattern="harassment"
    )

    result = match_l1_rules(
        db_session,
        sender_handle="a@example.com",
        subject="FYI",
        text="reporting harassment from a coworker",
    )

    assert result is not None
    assert result.bucket_name == "escalation"


def test_sender_allowlist_rule_matches_exact_handle(db_session):
    _make_bucket_with_rule(
        db_session, bucket_name="vip", rule_type="sender_allowlist", pattern="ceo@partner.com"
    )

    result = match_l1_rules(db_session, sender_handle="CEO@partner.com", subject=None, text=None)

    assert result is not None
    assert result.bucket_name == "vip"


def test_domain_rule_matches_sender_domain(db_session):
    _make_bucket_with_rule(
        db_session, bucket_name="vendor_invoice", rule_type="domain", pattern="billing-partner.com"
    )

    result = match_l1_rules(
        db_session, sender_handle="ap@billing-partner.com", subject="Invoice #42", text=None
    )

    assert result is not None
    assert result.bucket_name == "vendor_invoice"


def test_no_match_returns_none(db_session):
    _make_bucket_with_rule(
        db_session, bucket_name="escalation", rule_type="keyword", pattern="urgent"
    )

    result = match_l1_rules(
        db_session,
        sender_handle="a@example.com",
        subject="routine update",
        text="nothing to see here",
    )

    assert result is None


def test_inactive_rule_is_ignored(db_session):
    bucket = Bucket(name="escalation", description="escalation", is_active=True)
    db_session.add(bucket)
    db_session.flush()
    db_session.add(
        Rule(bucket_id=bucket.id, rule_type="keyword", pattern="urgent", is_active=False)
    )
    db_session.commit()

    result = match_l1_rules(
        db_session, sender_handle="a@example.com", subject="URGENT", text=None
    )

    assert result is None
