import pytest
from moto import mock_aws

from labeler.infra.storage.local_adapter import LocalStore
from labeler.infra.storage.port import get_json, put_json
from labeler.infra.storage.s3_adapter import S3Store


def test_local_store_round_trip_and_refuses_path_escape(tmp_path):
    store = LocalStore(tmp_path / "objects")
    put_json(store, "traces/v1/1.json", {"a": 1})
    assert get_json(store, "traces/v1/1.json") == {"a": 1}
    assert store.get_bytes("missing.json") is None
    with pytest.raises(ValueError):
        store.put_bytes("../outside.txt", b"x")


@mock_aws
def test_s3_store_creates_bucket_and_round_trips():
    store = S3Store("labeler-test", endpoint_url=None, region="us-east-1")
    store.ensure_bucket()
    store.ensure_bucket()  # idempotent
    put_json(store, "traces/v2/9.json", {"tier": "tier2"})
    assert get_json(store, "traces/v2/9.json") == {"tier": "tier2"}
    assert store.get_bytes("nope") is None
