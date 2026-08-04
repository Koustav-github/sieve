import json

import pytest

from app.classify.seed import load_seed_data, seed_buckets_and_rules
from app.models.bucket import Bucket
from app.models.rule import Rule


def test_seed_buckets_and_rules_creates_rows(db_session, tmp_path):
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(
        json.dumps(
            {
                "buckets": [{"name": "support_general", "description": "General support"}],
                "rules": [
                    {"bucket": "support_general", "rule_type": "keyword", "pattern": "refund"}
                ],
            }
        )
    )

    seed_buckets_and_rules(db_session, path=seed_file)

    bucket = db_session.query(Bucket).filter_by(name="support_general").one()
    assert bucket.description == "General support"
    rule = db_session.query(Rule).filter_by(pattern="refund").one()
    assert rule.bucket_id == bucket.id


def test_seed_is_idempotent(db_session, tmp_path):
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(
        json.dumps(
            {
                "buckets": [{"name": "support_general", "description": "General support"}],
                "rules": [
                    {"bucket": "support_general", "rule_type": "keyword", "pattern": "refund"}
                ],
            }
        )
    )

    seed_buckets_and_rules(db_session, path=seed_file)
    seed_buckets_and_rules(db_session, path=seed_file)

    assert db_session.query(Bucket).count() == 1
    assert db_session.query(Rule).count() == 1


def test_seed_updates_existing_bucket_description(db_session, tmp_path):
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(
        json.dumps({"buckets": [{"name": "support_general", "description": "v1"}]})
    )
    seed_buckets_and_rules(db_session, path=seed_file)

    seed_file.write_text(
        json.dumps({"buckets": [{"name": "support_general", "description": "v2"}]})
    )
    seed_buckets_and_rules(db_session, path=seed_file)

    bucket = db_session.query(Bucket).filter_by(name="support_general").one()
    assert bucket.description == "v2"


def test_load_seed_data_raises_on_missing_file(tmp_path):
    with pytest.raises(RuntimeError):
        load_seed_data(tmp_path / "does_not_exist.json")


def test_load_seed_data_raises_on_empty_buckets(tmp_path):
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps({"buckets": []}))

    with pytest.raises(RuntimeError):
        load_seed_data(seed_file)


def test_seed_raises_on_rule_referencing_unknown_bucket(db_session, tmp_path):
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(
        json.dumps(
            {
                "buckets": [{"name": "support_general", "description": "General support"}],
                "rules": [{"bucket": "does_not_exist", "rule_type": "keyword", "pattern": "x"}],
            }
        )
    )

    with pytest.raises(RuntimeError):
        seed_buckets_and_rules(db_session, path=seed_file)
