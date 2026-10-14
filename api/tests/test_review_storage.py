from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from app.services import object_storage as storage


def _error(code):
    return ClientError({"Error": {"Code": code}}, "test")


@pytest.mark.parametrize(
    "method,code",
    [
        ("get_document", "NoSuchKey"),
        ("get_document", "AccessDenied"),
        ("put_document", "AccessDenied"),
        ("delete_document_strict", "AccessDenied"),
    ],
)
def test_storage_normalizes_service_errors(monkeypatch, method, code):
    client = Mock()
    client.get_object.side_effect = _error(code)
    client.put_object.side_effect = _error(code)
    client.delete_object.side_effect = _error(code)
    monkeypatch.setattr(storage, "_client", lambda: client)
    with pytest.raises(storage.StorageUnavailableError):
        if method == "put_document":
            storage.put_document(
                object_key="key", content=b"x", content_type="text/plain", metadata={}
            )
        else:
            getattr(storage, method)("key")


def test_best_effort_cleanup_does_not_mask_database_failure(monkeypatch):
    client = Mock()
    client.delete_object.side_effect = _error("AccessDenied")
    monkeypatch.setattr(storage, "_client", lambda: client)
    storage.delete_document("key")


@pytest.mark.parametrize(
    "code,success",
    [("BucketAlreadyOwnedByYou", True), ("BucketAlreadyExists", False), ("AccessDenied", False)],
)
def test_bucket_creation_races_only_accept_our_bucket(monkeypatch, code, success):
    client = Mock()
    client.head_bucket.side_effect = _error("NoSuchBucket")
    client.create_bucket.side_effect = _error(code)
    monkeypatch.setattr(storage, "_client", lambda: client)
    if success:
        storage.ensure_bucket()
    else:
        with pytest.raises(storage.StorageUnavailableError):
            storage.ensure_bucket()
