from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bucket import Bucket
from app.models.rule import Rule


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
    Keyword rules match case-insensitively against subject+text; sender_allowlist
    matches the full sender_handle exactly; domain matches the part of
    sender_handle after '@', case-insensitively.
    """
    haystack = " ".join(filter(None, [subject, text])).lower()
    domain = sender_handle.rsplit("@", 1)[-1].lower() if "@" in sender_handle else None

    rules = (
        db.execute(select(Rule).join(Bucket).where(Rule.is_active.is_(True)).order_by(Rule.id))
        .scalars()
        .all()
    )

    for rule in rules:
        matched = False
        if rule.rule_type == "keyword":
            matched = rule.pattern.lower() in haystack
        elif rule.rule_type == "sender_allowlist":
            matched = rule.pattern.lower() == sender_handle.lower()
        elif rule.rule_type == "domain":
            matched = domain is not None and rule.pattern.lower() == domain

        if matched:
            return L1Match(
                bucket_id=rule.bucket_id,
                bucket_name=rule.bucket.name,
                reason=f"L1 {rule.rule_type} rule matched: {rule.pattern!r}",
            )

    return None
