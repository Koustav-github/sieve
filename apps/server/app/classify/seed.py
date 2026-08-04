import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.bucket import Bucket
from app.models.rule import RULE_TYPES, Rule

DEFAULT_SEED_PATH = Path(__file__).parent / "seed_data.json"


def load_seed_data(path: Path | None = None) -> dict:
    path = path or DEFAULT_SEED_PATH
    if not path.exists():
        raise RuntimeError(f"Classification seed file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    buckets = data.get("buckets")
    if not buckets:
        raise RuntimeError(f"Classification seed file has no buckets: {path}")
    for bucket in buckets:
        if "name" not in bucket or "description" not in bucket:
            raise RuntimeError(f"Bucket entry missing name/description: {bucket!r}")

    for rule in data.get("rules", []):
        if rule.get("rule_type") not in RULE_TYPES:
            raise RuntimeError(f"Rule has invalid rule_type: {rule!r}")
        if "pattern" not in rule or "bucket" not in rule:
            raise RuntimeError(f"Rule entry missing pattern/bucket: {rule!r}")

    return data


def seed_buckets_and_rules(db: Session, path: Path | None = None) -> None:
    """Idempotent: upserts buckets by name and skips rules that already exist
    as an identical (bucket, rule_type, pattern) triple, so this can safely
    run on every worker startup.

    The seed file is the only authoring surface for this data (not an API),
    so a bucket/rule removed from the file must stop being active in the DB
    on the next startup. We deactivate (is_active=False) rather than delete,
    so any routing_decisions.bucket_id FK referencing a removed bucket stays
    valid."""
    data = load_seed_data(path)

    bucket_by_name: dict[str, Bucket] = {b.name: b for b in db.query(Bucket).all()}
    for entry in data["buckets"]:
        existing = bucket_by_name.get(entry["name"])
        if existing is not None:
            existing.description = entry["description"]
            existing.is_active = entry.get("is_active", True)
        else:
            bucket = Bucket(
                name=entry["name"],
                description=entry["description"],
                is_active=entry.get("is_active", True),
            )
            db.add(bucket)
            db.flush()
            bucket_by_name[bucket.name] = bucket

    existing_rules = {(r.bucket.name, r.rule_type, r.pattern) for r in db.query(Rule).all()}
    for entry in data.get("rules", []):
        bucket = bucket_by_name.get(entry["bucket"])
        if bucket is None:
            raise RuntimeError(f"Rule references unknown bucket: {entry!r}")
        key = (bucket.name, entry["rule_type"], entry["pattern"])
        if key in existing_rules:
            continue
        db.add(
            Rule(
                bucket_id=bucket.id,
                rule_type=entry["rule_type"],
                pattern=entry["pattern"],
                is_active=entry.get("is_active", True),
            )
        )

    seed_bucket_names = {entry["name"] for entry in data["buckets"]}
    for name, bucket in bucket_by_name.items():
        if name not in seed_bucket_names:
            bucket.is_active = False

    seed_rule_keys = {
        (entry["bucket"], entry["rule_type"], entry["pattern"])
        for entry in data.get("rules", [])
    }
    for rule in db.query(Rule).all():
        key = (rule.bucket.name, rule.rule_type, rule.pattern)
        if key not in seed_rule_keys:
            rule.is_active = False

    db.commit()
