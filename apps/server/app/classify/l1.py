from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bucket import Bucket
from app.models.rule import Rule

# Below this partial_ratio score (0-100), a keyword rule's pattern is
# considered "not present" in the message even as a near-miss (typo, plural,
# minor rewording) - keeps the fuzzy signal from firing on unrelated text.
FUZZY_KEYWORD_THRESHOLD = 90


@dataclass
class L1Match:
    bucket_id: int
    bucket_name: str
    reason: str


def match_l1_rules(
    db: Session,
    *,
    sender_handle: str,
    subject: str | None,
    text: str | None,
) -> L1Match | None:
    """Evaluate active L1 rules against one message, in id order (first match
    wins - rule ordering is the owner's tie-break, not ours to second-guess).
    Keyword rules match case-insensitively against subject+text, either as an
    exact substring or as a fuzzy near-miss (typo, plural, minor rewording)
    scoring at or above FUZZY_KEYWORD_THRESHOLD; sender_allowlist matches the
    full sender_handle exactly; domain matches the part of sender_handle
    after '@', case-insensitively.
    """
    haystack = " ".join(filter(None, [subject, text])).lower()
    domain = sender_handle.rsplit("@", 1)[-1].lower() if "@" in sender_handle else None

    rules = (
        db.execute(
            select(Rule).join(Bucket).where(
                Rule.is_active.is_(True),
                Bucket.is_active.is_(True),
            ).order_by(Rule.id)
        )
        .scalars()
        .all()
    )

    for rule in rules:
        matched = False
        reason = f"L1 {rule.rule_type} rule matched: {rule.pattern!r}"
        if rule.rule_type == "keyword":
            pattern = rule.pattern.lower()
            if pattern in haystack:
                matched = True
            elif haystack and fuzz.partial_ratio(pattern, haystack) >= FUZZY_KEYWORD_THRESHOLD:
                matched = True
                reason = f"L1 keyword rule fuzzy-matched: {rule.pattern!r}"
        elif rule.rule_type == "sender_allowlist":
            matched = rule.pattern.lower() == sender_handle.lower()
        elif rule.rule_type == "domain":
            matched = domain is not None and rule.pattern.lower() == domain

        if matched:
            return L1Match(
                bucket_id=rule.bucket_id,
                bucket_name=rule.bucket.name,
                reason=reason,
            )

    return None
