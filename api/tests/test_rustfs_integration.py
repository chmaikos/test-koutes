from __future__ import annotations

import os
import uuid

import pytest

from app.services.object_storage import delete_document, get_document, put_document


@pytest.mark.skipif(
    os.getenv("RUN_RUSTFS_INTEGRATION") != "1",
    reason="set RUN_RUSTFS_INTEGRATION=1 with OBJECT_STORAGE_* to test RustFS",
)
def test_rustfs_document_round_trip():
    object_key = f"integration-tests/{uuid.uuid4().hex}.pdf"
    content = b"%PDF-1.7 RustFS integration test"
    try:
        put_document(
            object_key=object_key,
            content=content,
            content_type="application/pdf",
            metadata={"sha256": "integration-test"},
        )
        body, length = get_document(object_key)
        try:
            assert length == len(content)
            assert body.read() == content
        finally:
            body.close()
    finally:
        delete_document(object_key)
