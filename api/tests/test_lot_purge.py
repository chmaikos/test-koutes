from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite

from app.db import Base
from app.deps import get_current_user
from app.events import bus
from app.main import app
from app.models import (
    BarcodeEntityKind,
    BarcodeIdentity,
    Box,
    BoxEvent,
    BoxRequest,
    BoxRequestAttachment,
    BoxRequestComment,
    BoxRequestDirection,
    BoxRequestDiscrepancy,
    BoxRequestDiscrepancyPhoto,
    BoxRequestDiscrepancyType,
    BoxRequestDocument,
    BoxRequestDocumentType,
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestException,
    BoxRequestExceptionKind,
    BoxRequestItem,
    BoxRequestOrigin,
    BoxRequestStatus,
    InAppNotification,
    Lot,
    LotEvent,
    LotPurgeCleanupStatus,
    LotPurgeEvent,
    RequestEmailOutbox,
    UserRole,
)
from app.services import lots as lots_service
from app.services.boxes import create_box as _create_box
from app.services.boxes import reassign_box_lot
from app.services.lots import (
    LotPurgeConflictError,
    LotPurgeNotFoundError,
    _purge_box_lock_statement,
    _purge_lot_lock_statement,
    _purge_owned_lock_statements,
    _purge_request_item_lock_statement,
    _purge_request_lock_statement,
    analyze_lot_purge_eligibility,
    cleanup_lot_purge_objects,
    get_or_create_lot,
    purge_lot,
)
from app.services.requests import create_completed_receipt

create_box = _create_box


def _eligible_lot(session, make_user, *, receipt_count: int = 1):
    operator = make_user(UserRole.operator)
    boxes: list[Box] = []
    requests: list[BoxRequest] = []
    for index in range(receipt_count):
        box = create_box(
            session,
            user=operator,
            box_number=str(index + 1),
            lot="Purge Candidate",
            warehouse_id=1,
        )
        boxes.append(box)
        requests.append(
            create_completed_receipt(
                session,
                user=operator,
                warehouse_id=1,
                boxes=[box],
                origin=(
                    BoxRequestOrigin.manual_entry
                    if index % 2 == 0
                    else BoxRequestOrigin.xlsx_import
                ),
                note="self receipt",
            )
        )
    for box in boxes:
        box.archived_at = datetime.now(UTC)
    session.commit()
    return operator, boxes[0].lot_record, boxes, requests


def _codes(preview) -> set[str]:
    return {blocker.code for blocker in preview.blockers}


def test_eligible_multiple_exclusive_self_receipts_are_reported(session, make_user):
    _operator, lot, boxes, requests = _eligible_lot(
        session,
        make_user,
        receipt_count=2,
    )

    preview = analyze_lot_purge_eligibility(session, lot_id=lot.id)

    assert preview.eligible is True
    assert preview.blockers == []
    assert preview.lot_name == "Purge Candidate"
    assert preview.lot_version == lot.version
    assert preview.active_box_count == 0
    assert preview.archived_box_count == 2
    assert preview.archived_box_ids == sorted(box.id for box in boxes)
    assert preview.linked_request_ids == sorted(request.id for request in requests)
    assert [(item.item_count, item.lot_item_count) for item in preview.requests] == [
        (1, 1),
        (1, 1),
    ]
    assert preview.object_key_count == 0


def test_active_staged_and_mixed_lot_shapes_are_blocked(session, make_user):
    operator = make_user(UserRole.operator)
    candidate = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Candidate",
        warehouse_id=1,
    )
    sibling = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Sibling",
        warehouse_id=1,
    )
    mixed = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[candidate, sibling],
        origin=BoxRequestOrigin.manual_entry,
        note="mixed receipt",
    )
    staged = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.submitted,
        requester_user_id=operator.id,
        origin=BoxRequestOrigin.xlsx_import,
    )
    session.add(staged)
    session.flush()
    staged.root_request_id = staged.id
    session.add(
        BoxRequestItem(
            request_id=staged.id,
            position=1,
            lot_id=candidate.lot_id,
            lot=candidate.lot,
            box_number="future",
        )
    )
    session.commit()

    preview = analyze_lot_purge_eligibility(session, lot_id=candidate.lot_id)

    assert {"active_boxes", "staged_requests", "mixed_lot_receipt"} <= _codes(preview)
    summaries = {item.request_id: item for item in preview.requests}
    assert summaries[mixed.id].item_count == 2
    assert summaries[mixed.id].lot_item_count == 1
    assert summaries[staged.id].status == "submitted"


def test_receipts_require_concrete_complete_box_provenance(session, make_user):
    operator, lot, boxes, requests = _eligible_lot(session, make_user)
    unlinked = create_box(
        session,
        user=operator,
        box_number="2",
        lot_id=lot.id,
        warehouse_id=1,
    )
    unlinked.archived_at = datetime.now(UTC)
    session.add(
        BoxRequestItem(
            request_id=requests[0].id,
            position=2,
            lot_id=lot.id,
            lot=lot.name,
            box_number="missing",
        )
    )
    session.commit()

    preview = analyze_lot_purge_eligibility(session, lot_id=lot.id)

    assert {
        "incomplete_receipt_provenance",
        "unlinked_archived_boxes",
    } <= _codes(preview)
    unlinked_blocker = next(
        blocker
        for blocker in preview.blockers
        if blocker.code == "unlinked_archived_boxes"
    )
    assert [entity.entity_id for entity in unlinked_blocker.entities] == [unlinked.id]
    assert boxes[0].id not in {
        entity.entity_id for entity in unlinked_blocker.entities
    }


def test_returns_families_and_incoming_references_are_blocked(session, make_user):
    operator, lot, boxes, receipts = _eligible_lot(session, make_user)
    source = receipts[0]
    follow_up = BoxRequest(
        direction=BoxRequestDirection.return_,
        warehouse_id=1,
        target_warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.completed,
        requester_user_id=operator.id,
        origin=BoxRequestOrigin.workflow,
        source_inbound_request_id=source.id,
        parent_request_id=source.id,
        root_request_id=source.id,
        completed_at=datetime.now(UTC),
    )
    session.add(follow_up)
    session.flush()
    session.add_all(
        [
            BoxRequestItem(
                request_id=follow_up.id,
                position=1,
                box_id=boxes[0].id,
                lot_id=lot.id,
                lot=lot.name,
                box_number=boxes[0].box_number,
            ),
            BoxRequestEvent(
                request_id=follow_up.id,
                event_type=BoxRequestEventType.completed,
                to_status=BoxRequestStatus.completed,
                user_id=operator.id,
            ),
        ]
    )
    session.commit()

    preview = analyze_lot_purge_eligibility(session, lot_id=lot.id)

    assert {
        "unsupported_request_origin",
        "unsupported_request_direction",
        "request_family",
        "foreign_request_reference",
    } <= _codes(preview)


def test_reassignment_and_other_workflow_history_are_blocked(session, make_user):
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    source = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Source",
        warehouse_id=1,
    )
    receipt = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[source],
        origin=BoxRequestOrigin.manual_entry,
        note="source receipt",
    )
    target = get_or_create_lot(
        session,
        user=admin,
        name="Target",
        warehouse_id=1,
    )
    moved = reassign_box_lot(
        session,
        user=admin,
        box=source,
        lot_id=target.id,
        reason="incorrect lot",
        expected_version=source.lot_record.version,
    )
    moved.archived_at = datetime.now(UTC)
    session.add(
        BoxRequestAttachment(
            request_id=receipt.id,
            object_key="requests/source/evidence.txt",
            original_filename="evidence.txt",
            content_type="text/plain",
            size_bytes=1,
            sha256="0" * 64,
            uploaded_by_user_id=operator.id,
        )
    )
    session.commit()

    preview = analyze_lot_purge_eligibility(session, lot_id=target.id)

    assert {
        "box_reassignment_history",
        "foreign_request_reference",
    } <= _codes(preview)
    assert preview.object_key_count == 1
    assert session.scalar(select(BoxEvent).where(BoxEvent.box_id == moved.id)) is not None


def test_merge_sources_and_targets_are_never_eligible(session, make_user):
    _operator, source, _boxes, _requests = _eligible_lot(session, make_user)
    admin = make_user(UserRole.admin)
    target = get_or_create_lot(
        session,
        user=admin,
        name="Merge Target",
        warehouse_id=1,
    )
    source.normalized_name = None
    source.merged_into_lot_id = target.id
    source.merged_at = datetime.now(UTC)
    session.commit()

    source_preview = analyze_lot_purge_eligibility(session, lot_id=source.id)
    target_preview = analyze_lot_purge_eligibility(session, lot_id=target.id)

    assert "merged_tombstone" in _codes(source_preview)
    assert "merge_target" in _codes(target_preview)


def test_purge_analyzer_missing_lot_and_lock_sql(session):
    with pytest.raises(LotPurgeNotFoundError):
        analyze_lot_purge_eligibility(session, lot_id=404)

    statements = (
        _purge_lot_lock_statement(9),
        _purge_box_lock_statement(9),
        _purge_request_lock_statement([9, 3]),
        _purge_request_item_lock_statement([9, 3]),
        *_purge_owned_lock_statements(
            lot_id=9,
            box_ids=[9, 3],
            request_ids=[9, 3],
            discrepancy_ids=[9, 3],
            pallet_ids=[9, 3],
        ),
    )
    postgres_sql = [
        str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        for statement in statements
    ]
    sqlite_sql = [
        str(
            statement.compile(
                dialect=sqlite.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        for statement in statements
    ]

    assert postgres_sql[0].endswith("FOR UPDATE OF lots")
    assert postgres_sql[1].endswith("FOR UPDATE OF boxes")
    assert "ORDER BY box_requests.id" in postgres_sql[2]
    assert postgres_sql[2].endswith("FOR UPDATE OF box_requests")
    assert "ORDER BY box_request_items.id" in postgres_sql[3]
    assert postgres_sql[3].endswith("FOR UPDATE OF box_request_items")
    assert all("FOR UPDATE OF " in sql for sql in postgres_sql[4:])
    assert all("LEFT OUTER JOIN" not in sql for sql in postgres_sql[4:])
    assert {
        sql.rsplit("FOR UPDATE OF ", maxsplit=1)[1]
        for sql in postgres_sql[4:]
    } == {
        "box_request_discrepancies",
        "box_request_events",
        "box_request_exceptions",
        "box_request_documents",
        "box_request_comments",
        "box_request_attachments",
        "in_app_notifications",
        "request_email_outbox",
        "box_request_discrepancy_photos",
        "box_events",
        "pallet_events",
        "lot_events",
    }
    assert all("FOR UPDATE" not in sql for sql in sqlite_sql)


def test_purge_ledger_has_no_lot_foreign_key(session):
    table = LotPurgeEvent.__table__
    assert "lot_id" in table.c
    assert all(
        foreign_key.target_fullname != "lots.id"
        for foreign_key in table.foreign_keys
    )


def test_all_operational_inbound_foreign_keys_are_explicitly_accounted_for():
    inbound: dict[str, set[tuple[str, str, str | None]]] = {
        target: set() for target in ("box_requests", "boxes", "lots")
    }
    for table in Base.metadata.tables.values():
        for foreign_key in table.foreign_keys:
            target = foreign_key.column.table.name
            if target in inbound:
                inbound[target].add(
                    (
                        table.name,
                        foreign_key.parent.name,
                        foreign_key.ondelete,
                    )
                )

    assert inbound["box_requests"] == {
        ("box_requests", "source_inbound_request_id", "RESTRICT"),
        ("box_requests", "parent_request_id", "RESTRICT"),
        ("box_requests", "root_request_id", "RESTRICT"),
        ("box_request_items", "request_id", "CASCADE"),
        ("box_request_events", "request_id", "CASCADE"),
        ("box_request_exceptions", "request_id", "CASCADE"),
        ("box_request_documents", "request_id", "CASCADE"),
        ("box_request_comments", "request_id", "CASCADE"),
        ("box_request_attachments", "request_id", "CASCADE"),
        ("box_request_discrepancies", "request_id", "CASCADE"),
        ("in_app_notifications", "request_id", "CASCADE"),
        ("request_email_outbox", "request_id", "CASCADE"),
    }
    assert inbound["boxes"] == {
        ("box_events", "box_id", "CASCADE"),
        ("box_files", "box_id", "RESTRICT"),
        ("box_files", "lot_id", "RESTRICT"),
        ("box_request_items", "box_id", "RESTRICT"),
        ("box_request_discrepancies", "box_id", "SET NULL"),
    }
    assert inbound["lots"] == {
        ("lots", "merged_into_lot_id", "RESTRICT"),
        ("boxes", "lot_id", "RESTRICT"),
        ("box_files", "lot_id", "RESTRICT"),
        ("lot_events", "lot_id", "RESTRICT"),
        ("box_request_items", "lot_id", "RESTRICT"),
        ("pallets", "lot_id", "RESTRICT"),
    }


def _add_request_owned_graph(session, *, request, box, operator):
    document = BoxRequestDocument(
        request_id=request.id,
        document_type=BoxRequestDocumentType.other,
        erp_reference="ERP-1",
        object_key=f"requests/{request.id}/document.pdf",
        original_filename="document.pdf",
        content_type="application/pdf",
        size_bytes=4,
        sha256="1" * 64,
        uploaded_by_user_id=operator.id,
    )
    attachment = BoxRequestAttachment(
        request_id=request.id,
        object_key=f"requests/{request.id}/attachment.txt",
        original_filename="attachment.txt",
        content_type="text/plain",
        size_bytes=4,
        sha256="2" * 64,
        uploaded_by_user_id=operator.id,
    )
    comment = BoxRequestComment(
        request_id=request.id,
        author_user_id=operator.id,
        body="owned comment",
    )
    discrepancy = BoxRequestDiscrepancy(
        request_id=request.id,
        request_item_id=request.items[0].id,
        box_id=box.id,
        discrepancy_type=BoxRequestDiscrepancyType.damaged,
        notes="owned discrepancy",
        created_by_user_id=operator.id,
    )
    operational_exception = BoxRequestException(
        request_id=request.id,
        exception_kind=BoxRequestExceptionKind.hold,
        reason="owned exception",
        resume_target=BoxRequestStatus.completed,
        created_by_user_id=operator.id,
    )
    notification = InAppNotification(
        user_id=operator.id,
        request_id=request.id,
        warehouse_id=request.warehouse_id,
        kind="completed",
        title="Receipt completed",
        body="owned notification",
        deep_link=f"/requests/{request.id}",
        idempotency_key=f"purge-test-notification-{request.id}",
    )
    outbox = RequestEmailOutbox(
        request_id=request.id,
        recipient_user_id=operator.id,
        recipient_email=operator.email,
        kind="completed",
        subject="Receipt completed",
        html_body="<p>completed</p>",
        text_body="completed",
        idempotency_key=f"purge-test-email-{request.id}",
    )
    session.add_all(
        [
            document,
            attachment,
            comment,
            discrepancy,
            operational_exception,
            notification,
            outbox,
        ]
    )
    session.flush()
    photo = BoxRequestDiscrepancyPhoto(
        discrepancy_id=discrepancy.id,
        object_key=f"requests/{request.id}/photo.jpg",
        original_filename="photo.jpg",
        content_type="image/jpeg",
        size_bytes=4,
        sha256="3" * 64,
        uploaded_by_user_id=operator.id,
    )
    session.add(photo)
    session.commit()
    return {document.object_key, attachment.object_key, photo.object_key}


def test_transactional_purge_deletes_complete_owned_graph_and_preserves_sibling(
    session,
    make_user,
    monkeypatch,
):
    operator, lot, boxes, requests = _eligible_lot(session, make_user, receipt_count=2)
    sibling = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Sibling",
        warehouse_id=2,
    )
    sibling_request = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=2,
        boxes=[sibling],
        origin=BoxRequestOrigin.manual_entry,
        note="must survive",
    )
    expected_keys = _add_request_owned_graph(
        session,
        request=requests[0],
        box=boxes[0],
        operator=operator,
    )
    admin = make_user(UserRole.admin)
    lot_id = lot.id
    box_ids = [box.id for box in boxes]
    identity_ids = [lot.barcode_identity_id, *[box.barcode_identity_id for box in boxes]]
    request_ids = [request.id for request in requests]
    deleted_keys: list[str] = []
    monkeypatch.setattr(
        lots_service,
        "delete_document_strict",
        lambda key: deleted_keys.append(key),
    )
    preview = analyze_lot_purge_eligibility(session, lot_id=lot.id)
    assert preview.eligible
    assert preview.object_key_count == 3

    result = purge_lot(
        session,
        user=admin,
        lot_id=lot.id,
        confirmation_name=lot.name,
        reason="Duplicate self-receipt",
        expected_version=lot.version,
        expected_graph_signature=preview.graph_signature,
    )
    cleanup = cleanup_lot_purge_objects(
        session,
        user=admin,
        audit_id=result.audit_id,
    )

    assert cleanup.status == LotPurgeCleanupStatus.completed
    assert set(deleted_keys) == expected_keys
    assert session.get(Lot, lot_id) is None
    assert session.get(BoxRequest, sibling_request.id) is not None
    assert session.get(Box, sibling.id) is not None
    retired_identities = list(
        session.scalars(
            select(BarcodeIdentity)
            .where(BarcodeIdentity.id.in_(identity_ids))
            .order_by(BarcodeIdentity.id)
        ).all()
    )
    assert len(retired_identities) == len(identity_ids)
    assert all(identity.retired_at is not None for identity in retired_identities)
    assert {
        identity.retirement_metadata["operation"] for identity in retired_identities
    } == {"lot_purge"}
    assert {
        identity.entity_kind for identity in retired_identities
    } == {BarcodeEntityKind.lot, BarcodeEntityKind.box}
    for model in (
        BoxRequestItem,
        BoxRequestEvent,
        BoxRequestDocument,
        BoxRequestAttachment,
        BoxRequestComment,
        BoxRequestException,
        BoxRequestDiscrepancy,
        BoxRequestDiscrepancyPhoto,
        InAppNotification,
        RequestEmailOutbox,
    ):
        request_column = (
            model.request_id
            if hasattr(model, "request_id")
            else BoxRequestDiscrepancyPhoto.discrepancy_id
        )
        if model is BoxRequestDiscrepancyPhoto:
            assert session.scalar(select(func.count()).select_from(model)) == 0
        else:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(request_column.in_(request_ids))
                )
                == 0
            )
    assert (
        session.scalar(
            select(func.count()).select_from(BoxEvent).where(BoxEvent.box_id.in_(box_ids))
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count()).select_from(LotEvent).where(LotEvent.lot_id == lot_id)
        )
        == 0
    )
    audit = session.get(LotPurgeEvent, result.audit_id)
    assert audit is not None
    assert audit.lot_id == lot_id
    assert audit.receipt_ids == sorted(request_ids)
    assert audit.archived_box_ids == sorted(box_ids)
    assert audit.object_cleanup_status == LotPurgeCleanupStatus.completed
    assert audit.event_metadata["deleted_counts"]["box_requests"] == 2
    assert audit.event_metadata["deleted_counts"]["boxes"] == 2


def test_purge_rejects_confirmation_version_and_graph_races(
    session,
    make_user,
    monkeypatch,
):
    operator, lot, _boxes, requests = _eligible_lot(session, make_user)
    admin = make_user(UserRole.admin)
    with pytest.raises(LotPurgeConflictError) as mismatch:
        purge_lot(
            session,
            user=admin,
            lot_id=lot.id,
            confirmation_name=lot.name.lower(),
            reason="wrong confirmation",
            expected_version=lot.version,
        )
    assert mismatch.value.code == "confirmation_name_mismatch"
    session.rollback()

    with pytest.raises(LotPurgeConflictError) as stale:
        purge_lot(
            session,
            user=admin,
            lot_id=lot.id,
            confirmation_name=lot.name,
            reason="stale version",
            expected_version=lot.version + 1,
        )
    assert stale.value.code == "version_conflict"
    session.rollback()

    original = lots_service.analyze_lot_purge_eligibility
    calls = 0

    def racing_analyzer(db, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            db.add(
                BoxRequestComment(
                    request_id=requests[0].id,
                    author_user_id=operator.id,
                    body="racing graph change",
                )
            )
            db.flush()
        return original(db, **kwargs)

    monkeypatch.setattr(lots_service, "analyze_lot_purge_eligibility", racing_analyzer)
    with pytest.raises(LotPurgeConflictError) as changed:
        purge_lot(
            session,
            user=admin,
            lot_id=lot.id,
            confirmation_name=lot.name,
            reason="graph race",
            expected_version=lot.version,
        )
    assert changed.value.code == "graph_changed"
    session.rollback()
    assert session.get(Lot, lot.id) is not None


def test_purge_is_atomic_when_commit_fails(session, make_user, monkeypatch):
    _operator, lot, boxes, requests = _eligible_lot(session, make_user)
    admin = make_user(UserRole.admin)

    def fail_commit():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="database unavailable"):
        purge_lot(
            session,
            user=admin,
            lot_id=lot.id,
            confirmation_name=lot.name,
            reason="rollback test",
            expected_version=lot.version,
        )

    assert session.get(Lot, lot.id) is not None
    assert session.get(Box, boxes[0].id) is not None
    assert session.get(BoxRequest, requests[0].id) is not None
    assert session.scalar(select(func.count()).select_from(LotPurgeEvent)) == 0


def test_purge_rolls_back_when_db_deletion_raises(session, make_user, monkeypatch):
    _operator, lot, boxes, requests = _eligible_lot(session, make_user)
    admin = make_user(UserRole.admin)
    original = lots_service._delete_count
    calls = 0

    def fail_during_deletion(db, statement):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("delete failed")
        return original(db, statement)

    monkeypatch.setattr(lots_service, "_delete_count", fail_during_deletion)
    with pytest.raises(RuntimeError, match="delete failed"):
        purge_lot(
            session,
            user=admin,
            lot_id=lot.id,
            confirmation_name=lot.name,
            reason="rollback deletion test",
            expected_version=lot.version,
        )

    assert session.get(Lot, lot.id) is not None
    assert session.get(Box, boxes[0].id) is not None
    assert session.get(BoxRequest, requests[0].id) is not None
    assert session.scalar(select(func.count()).select_from(LotPurgeEvent)) == 0


def test_shared_object_key_blocks_purge_and_cleanup_never_deletes_sibling_key(
    session,
    make_user,
    monkeypatch,
):
    operator, lot, boxes, requests = _eligible_lot(session, make_user)
    shared_key = "requests/shared/object.pdf"
    session.add(
        BoxRequestDocument(
            request_id=requests[0].id,
            document_type=BoxRequestDocumentType.other,
            erp_reference="ERP-CANDIDATE",
            object_key=shared_key,
            original_filename="candidate.pdf",
            content_type="application/pdf",
            size_bytes=1,
            sha256="a" * 64,
            uploaded_by_user_id=operator.id,
        )
    )
    sibling_box = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Sibling object lot",
        warehouse_id=2,
    )
    sibling_request = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=2,
        boxes=[sibling_box],
        origin=BoxRequestOrigin.manual_entry,
        note="sibling",
    )
    session.add(
        BoxRequestAttachment(
            request_id=sibling_request.id,
            object_key=shared_key,
            original_filename="sibling.pdf",
            content_type="application/pdf",
            size_bytes=1,
            sha256="b" * 64,
            uploaded_by_user_id=operator.id,
        )
    )
    session.commit()

    blocked = analyze_lot_purge_eligibility(session, lot_id=lot.id)
    assert "shared_object_key" in _codes(blocked)

    # Remove the pre-existing conflict, purge the DB graph, then introduce a
    # racing sibling reference before post-commit object cleanup.
    sibling_attachment = session.scalar(
        select(BoxRequestAttachment).where(
            BoxRequestAttachment.request_id == sibling_request.id
        )
    )
    session.delete(sibling_attachment)
    session.commit()
    preview = analyze_lot_purge_eligibility(session, lot_id=lot.id)
    assert preview.eligible
    admin = make_user(UserRole.admin)
    result = purge_lot(
        session,
        user=admin,
        lot_id=lot.id,
        confirmation_name=lot.name,
        reason="shared storage race test",
        expected_version=lot.version,
        expected_graph_signature=preview.graph_signature,
    )
    session.add(
        BoxRequestAttachment(
            request_id=sibling_request.id,
            object_key=shared_key,
            original_filename="racing-sibling.pdf",
            content_type="application/pdf",
            size_bytes=1,
            sha256="c" * 64,
            uploaded_by_user_id=operator.id,
        )
    )
    session.commit()
    deleted_keys: list[str] = []
    monkeypatch.setattr(
        lots_service,
        "delete_document_strict",
        lambda key: deleted_keys.append(key),
    )

    cleanup = cleanup_lot_purge_objects(
        session,
        user=admin,
        audit_id=result.audit_id,
    )

    assert cleanup.status == LotPurgeCleanupStatus.failed
    assert deleted_keys == []
    assert cleanup.failures == [
        {
            "object_key": shared_key,
            "error": (
                "object storage deletion skipped because the key is still referenced"
            ),
        }
    ]
    assert session.get(BoxRequest, sibling_request.id) is not None
    assert session.get(Box, sibling_box.id) is not None


def test_admin_api_preview_purge_storage_failure_retry_and_scoped_sse(
    client,
    session,
    make_user,
    monkeypatch,
):
    operator, lot, boxes, requests = _eligible_lot(session, make_user)
    keys = _add_request_owned_graph(
        session,
        request=requests[0],
        box=boxes[0],
        operator=operator,
    )
    published: list[tuple[str, dict[str, object]]] = []

    async def capture(event_type, data):
        published.append((event_type, data))

    monkeypatch.setattr(bus, "publish", capture)
    monkeypatch.setattr(
        lots_service,
        "delete_document_strict",
        lambda _key: (_ for _ in ()).throw(RuntimeError("storage down")),
    )
    preview_response = client.get(f"/api/lots/{lot.id}/purge-preview")
    assert preview_response.status_code == 200
    preview = preview_response.json()
    lot_id = lot.id
    lot_name = lot.name
    lot_version = lot.version
    assert preview["eligible"] is True
    assert preview["confirmation_policy"] == "exact_case_sensitive_no_normalization"

    wrong_name = client.post(
        f"/api/lots/{lot_id}/purge",
        json={
            "confirmation_name": lot_name.lower(),
            "reason": "wrong name",
            "expected_version": lot_version,
        },
    )
    assert wrong_name.status_code == 409
    assert wrong_name.json()["detail"]["code"] == "confirmation_name_mismatch"
    stale = client.post(
        f"/api/lots/{lot_id}/purge",
        json={
            "confirmation_name": lot_name,
            "reason": "stale",
            "expected_version": lot_version + 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "version_conflict"

    response = client.post(
        f"/api/lots/{lot_id}/purge",
        json={
            "confirmation_name": lot_name,
            "reason": "Sensitive administrative reason",
            "expected_version": lot_version,
            "expected_graph_signature": preview["graph_signature"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object_cleanup_status"] == "failed"
    assert {failure["object_key"] for failure in body["object_cleanup_failures"]} == keys
    assert all(event_type == "lot.purged" for event_type, _data in published)
    assert {data["warehouse_id"] for _event_type, data in published} == {1}
    assert all("reason" not in data for _event_type, data in published)

    monkeypatch.setattr(lots_service, "delete_document_strict", lambda _key: None)
    retry = client.post(
        f"/api/lots/purge-audits/{body['purge_audit_id']}/cleanup-retry"
    )
    assert retry.status_code == 200
    assert retry.json()["object_cleanup_status"] == "completed"
    repeated = client.post(
        f"/api/lots/{lot_id}/purge",
        json={
            "confirmation_name": lot_name,
            "reason": "repeat",
            "expected_version": lot_version,
        },
    )
    assert repeated.status_code == 404


def test_purge_routes_are_admin_only(client, session, make_user):
    _operator, lot, _boxes, _requests = _eligible_lot(session, make_user)
    viewer = make_user(UserRole.viewer)
    app.dependency_overrides[get_current_user] = lambda: viewer

    assert client.get(f"/api/lots/{lot.id}/purge-preview").status_code == 403
    response = client.post(
        f"/api/lots/{lot.id}/purge",
        json={
            "confirmation_name": lot.name,
            "reason": "not allowed",
            "expected_version": lot.version,
        },
    )
    assert response.status_code == 403
