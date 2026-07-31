from __future__ import annotations

from functools import lru_cache
from typing import Protocol

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings


class StorageUnavailableError(RuntimeError):
    pass


class ReadableBody(Protocol):
    def read(self, amt: int | None = None) -> bytes: ...

    def close(self) -> None: ...


@lru_cache(maxsize=1)
def _client() -> BaseClient:
    settings = get_settings()
    if not settings.object_storage_configured:
        raise StorageUnavailableError("object storage is not configured")
    endpoint = settings.object_storage_endpoint.rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        scheme = "https" if settings.object_storage_secure else "http"
        endpoint = f"{scheme}://{endpoint}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.object_storage_region,
        aws_access_key_id=settings.object_storage_access_key,
        aws_secret_access_key=settings.object_storage_secret_key,
    )


def ensure_bucket() -> None:
    settings = get_settings()
    client = _client()
    try:
        client.head_bucket(Bucket=settings.object_storage_bucket)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise StorageUnavailableError("unable to access document bucket") from exc
        try:
            client.create_bucket(Bucket=settings.object_storage_bucket)
        except BotoCoreError as create_exc:
            raise StorageUnavailableError("unable to create document bucket") from create_exc
    except BotoCoreError as exc:
        raise StorageUnavailableError("unable to access document bucket") from exc


def put_document(
    *,
    object_key: str,
    content: bytes,
    content_type: str,
    metadata: dict[str, str],
) -> None:
    settings = get_settings()
    ensure_bucket()
    try:
        _client().put_object(
            Bucket=settings.object_storage_bucket,
            Key=object_key,
            Body=content,
            ContentType=content_type,
            Metadata=metadata,
        )
    except BotoCoreError as exc:
        raise StorageUnavailableError("document upload failed") from exc


def get_document(object_key: str) -> tuple[ReadableBody, int]:
    settings = get_settings()
    try:
        response = _client().get_object(
            Bucket=settings.object_storage_bucket,
            Key=object_key,
        )
    except BotoCoreError as exc:
        raise StorageUnavailableError("document download failed") from exc
    return response["Body"], int(response.get("ContentLength") or 0)


def delete_document(object_key: str) -> None:
    settings = get_settings()
    try:
        _client().delete_object(Bucket=settings.object_storage_bucket, Key=object_key)
    except (BotoCoreError, StorageUnavailableError):
        # Best-effort cleanup after a DB failure; an orphan is preferable to
        # masking the original transactional error.
        return
