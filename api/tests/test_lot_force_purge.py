from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

from app.deps import get_current_user
from app.events import bus
from app.main import app
from app.models import (
    Box,
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
    LotPurgeEvent,
    RequestEmailOutbox,
    UserRole,
)
from app.services import lots as lots_service
from app.services.boxes import create_box
from app.services.lots import (
    _force_purge_box_lock_statement,
    _force_purge_discrepancy_lock_statement,
    _force_purge_item_lock_statement,
    _force_purge_lot_lock_statement,
    _force_purge_request_lock_statement,
    analyze_lot_force_purge_impact,
    cleanup_lot_purge_objects,
    force_purge_lot,
    get_or_create_lot,
)
from app.services.requests import create_completed_receipt


def _request(
    session,
    *,
    operator,
    direction,
    status,
    items,
    quantity=None,
    actual=None,
    variance=None,
    origin=BoxRequestOrigin.workflow,
    source_id=None,
    parent_id=None,
    root_id=None,
):
    request = BoxRequest(
        direction=direction,
        warehouse_id=1,
        quantity=quantity if quantity is not None else len(items),
        status=status,
        requester_user_id=operator.id,
        origin=origin,
        actual_received_quantity=actual,
        variance_quantity=variance,
        source_inbound_request_id=source_id,
        parent_request_id=parent_id,
        root_request_id=root_id,
        completed_at=datetime.now(UTC)
        if status == BoxRequestStatus.completed
        else None,
    )
    session.add(request)
    session.flush()
    if root_id is None:
        request.root_request_id = request.id
    for position, box in enumerate(items, start=1):
        session.add(
            BoxRequestItem(
                request_id=request.id,
                position=position,
                box_id=box.id,
                lot_id=box.lot_id,
                lot=box.lot,
                box_number=box.box_number,
            )
        )
    session.flush()
    return request


def _photo(session, *, discrepancy, key, operator):
    photo = BoxRequestDiscrepancyPhoto(
        discrepancy_id=discrepancy.id,
        object_key=key,
        original_filename="evidence.jpg",
        content_type="image/jpeg",
        size_bytes=1,
        sha256="a" * 64,
        uploaded_by_user_id=operator.id,
    )
    session.add(photo)
    session.flush()
    return photo


def test_force_impact_rewrites_mixed_receipt_and_scopes_discrepancies(
    session,
    make_user,
):
    operator = make_user(UserRole.operator)
    selected = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Selected",
        warehouse_id=1,
    )
    sibling = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Sibling",
        warehouse_id=1,
    )
    receipt = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[selected, sibling],
        origin=BoxRequestOrigin.manual_entry,
        note="mixed receipt",
    )
    selected_item, sibling_item = sorted(receipt.items, key=lambda item: item.position)
    selected_discrepancy = BoxRequestDiscrepancy(
        request_id=receipt.id,
        request_item_id=selected_item.id,
        box_id=selected.id,
        discrepancy_type=BoxRequestDiscrepancyType.damaged,
        created_by_user_id=operator.id,
    )
    sibling_discrepancy = BoxRequestDiscrepancy(
        request_id=receipt.id,
        request_item_id=sibling_item.id,
        box_id=sibling.id,
        discrepancy_type=BoxRequestDiscrepancyType.damaged,
        created_by_user_id=operator.id,
    )
    session.add_all([selected_discrepancy, sibling_discrepancy])
    session.flush()
    selected_photo = _photo(
        session,
        discrepancy=selected_discrepancy,
        key="force/selected.jpg",
        operator=operator,
    )
    sibling_photo = _photo(
        session,
        discrepancy=sibling_discrepancy,
        key="force/sibling.jpg",
        operator=operator,
    )
    session.commit()

    impact = analyze_lot_force_purge_impact(session, lot_id=selected.lot_id)
    repeated = analyze_lot_force_purge_impact(session, lot_id=selected.lot_id)
    locked = analyze_lot_force_purge_impact(
        session,
        lot_id=selected.lot_id,
        lock_for_update=True,
    )

    assert impact.force_allowed is True
    assert impact.confirmation_phrase == f"FORCE DELETE LOT {selected.lot_id}"
    assert impact.active_box_ids == [selected.id]
    assert impact.fully_deleted_request_ids == []
    assert impact.graph_signature == repeated.graph_signature
    assert impact.graph_signature == locked.graph_signature
    rewrite = impact.request_rewrites[0]
    assert rewrite.request_id == receipt.id
    assert rewrite.adjustment_event_type == "force_purge_adjusted"
    assert (rewrite.before_item_count, rewrite.after_item_count) == (2, 1)
    assert (rewrite.before_quantity, rewrite.after_quantity) == (2, 1)
    assert (
        rewrite.before_actual_received_quantity,
        rewrite.after_actual_received_quantity,
    ) == (2, 1)
    assert (rewrite.before_variance_quantity, rewrite.after_variance_quantity) == (
        0,
        0,
    )
    assert rewrite.removed_item_ids == [selected_item.id]
    assert rewrite.removed_discrepancy_ids == [selected_discrepancy.id]
    assert rewrite.removed_discrepancy_photo_ids == [selected_photo.id]
    assert rewrite.preserved_sibling_lot_ids == [sibling.lot_id]
    assert [
        (position.item_id, position.before_position, position.after_position)
        for position in rewrite.item_positions
    ] == [
        (selected_item.id, 1, None),
        (sibling_item.id, 2, 1),
    ]
    assert impact.object_cleanup.deletable_keys == [selected_photo.object_key]
    assert sibling_photo.object_key not in impact.object_cleanup.deletable_keys
    assert {"active_boxes", "mixed_lot_receipt"} <= {
        blocker.code for blocker in impact.overridden_blockers
    }


def test_force_impact_partitions_zero_items_and_detaches_all_lineage(
    session,
    make_user,
):
    operator = make_user(UserRole.operator)
    selected = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Selected lineage",
        warehouse_id=1,
    )
    deleted_request = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[selected],
        origin=BoxRequestOrigin.manual_entry,
        note="will be deleted",
    )
    sibling = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Lineage sibling",
        warehouse_id=1,
    )
    preserved = _request(
        session,
        operator=operator,
        direction=BoxRequestDirection.return_,
        status=BoxRequestStatus.submitted,
        items=[sibling],
        source_id=deleted_request.id,
        parent_id=deleted_request.id,
        root_id=deleted_request.id,
    )
    shared_key = "force/shared.pdf"
    session.add_all(
        [
            BoxRequestDocument(
                request_id=deleted_request.id,
                document_type=BoxRequestDocumentType.other,
                erp_reference="DELETE",
                object_key=shared_key,
                original_filename="delete.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="b" * 64,
                uploaded_by_user_id=operator.id,
            ),
            BoxRequestAttachment(
                request_id=preserved.id,
                object_key=shared_key,
                original_filename="preserve.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="c" * 64,
                uploaded_by_user_id=operator.id,
            ),
        ]
    )
    session.commit()

    impact = analyze_lot_force_purge_impact(session, lot_id=selected.lot_id)

    assert impact.fully_deleted_request_ids == [deleted_request.id]
    assert impact.request_rewrites == []
    assert [
        (
            detachment.request_id,
            detachment.field_name,
            detachment.deleted_target_request_id,
        )
        for detachment in impact.incoming_lineage_detachments
    ] == [
        (preserved.id, "source_inbound_request_id", deleted_request.id),
        (preserved.id, "parent_request_id", deleted_request.id),
        (preserved.id, "root_request_id", deleted_request.id),
    ]
    assert impact.object_cleanup.deletable_keys == []
    assert impact.object_cleanup.shared_skipped_keys == [shared_key]
    assert "shared_object_key" not in {
        blocker.code for blocker in impact.overridden_blockers
    }


def test_force_impact_recalculates_completed_and_open_returns(
    session,
    make_user,
):
    operator = make_user(UserRole.operator)
    selected = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Return selected",
        warehouse_id=1,
    )
    sibling = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Return sibling",
        warehouse_id=1,
    )
    completed = _request(
        session,
        operator=operator,
        direction=BoxRequestDirection.return_,
        status=BoxRequestStatus.completed,
        items=[selected, sibling],
        actual=1,
        variance=-1,
    )
    selected_completed_item = completed.items[0]
    session.add(
        BoxRequestDiscrepancy(
            request_id=completed.id,
            request_item_id=selected_completed_item.id,
            box_id=selected.id,
            discrepancy_type=BoxRequestDiscrepancyType.missing,
            quantity=1,
            created_by_user_id=operator.id,
        )
    )
    opened = _request(
        session,
        operator=operator,
        direction=BoxRequestDirection.return_,
        status=BoxRequestStatus.approved,
        items=[selected, sibling],
    )
    session.commit()

    impact = analyze_lot_force_purge_impact(session, lot_id=selected.lot_id)
    rewrites = {rewrite.request_id: rewrite for rewrite in impact.request_rewrites}

    assert (
        rewrites[completed.id].after_quantity,
        rewrites[completed.id].after_actual_received_quantity,
        rewrites[completed.id].after_variance_quantity,
    ) == (1, 1, 0)
    assert (
        rewrites[opened.id].after_quantity,
        rewrites[opened.id].after_actual_received_quantity,
        rewrites[opened.id].after_variance_quantity,
    ) == (1, None, None)
    assert all(rewrite.after_quantity > 0 for rewrite in rewrites.values())


def test_force_impact_keeps_merge_relations_as_hard_blocks(session, make_user):
    operator = make_user(UserRole.operator)
    selected = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Merged source",
        warehouse_id=1,
    )
    target = get_or_create_lot(
        session,
        user=operator,
        name="Merged target",
        warehouse_id=1,
    )
    selected.lot_record.normalized_name = None
    selected.lot_record.merged_into_lot_id = target.id
    selected.lot_record.merged_at = datetime.now(UTC)
    session.commit()

    source_impact = analyze_lot_force_purge_impact(
        session, lot_id=selected.lot_id
    )
    target_impact = analyze_lot_force_purge_impact(session, lot_id=target.id)

    assert source_impact.force_allowed is False
    assert [blocker.code for blocker in source_impact.hard_blockers] == [
        "merged_tombstone"
    ]
    assert target_impact.force_allowed is False
    assert [blocker.code for blocker in target_impact.hard_blockers] == [
        "merge_target"
    ]


def test_force_impact_lock_statements_have_explicit_ordered_targets():
    statements = (
        _force_purge_lot_lock_statement(9),
        _force_purge_box_lock_statement(9),
        _force_purge_request_lock_statement([9, 3]),
        _force_purge_item_lock_statement([9, 3]),
        _force_purge_discrepancy_lock_statement([9, 3]),
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
    assert "ORDER BY box_request_discrepancies.id" in postgres_sql[4]
    assert postgres_sql[4].endswith(
        "FOR UPDATE OF box_request_discrepancies"
    )
    assert all("FOR UPDATE" not in sql for sql in sqlite_sql)


def _execute_force_purge(session, *, admin, preview, reason):
    return force_purge_lot(
        session,
        user=admin,
        lot_id=preview.lot_id,
        confirmation_name=preview.lot_name,
        confirmation_phrase=preview.confirmation_phrase,
        reason=reason,
        expected_version=preview.lot_version,
        expected_graph_signature=preview.graph_signature,
        acknowledged_blocker_codes=[
            blocker.code for blocker in preview.overridden_blockers
        ],
    )


def test_force_purge_surgically_rewrites_mixed_request(
    session,
    make_user,
    monkeypatch,
):
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    selected = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Execute selected",
        warehouse_id=1,
    )
    sibling = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Execute sibling",
        warehouse_id=1,
    )
    request = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[selected, sibling],
        origin=BoxRequestOrigin.manual_entry,
        note="mixed execution",
    )
    selected_item, sibling_item = sorted(request.items, key=lambda item: item.position)
    selected_discrepancy = BoxRequestDiscrepancy(
        request_id=request.id,
        request_item_id=selected_item.id,
        box_id=selected.id,
        discrepancy_type=BoxRequestDiscrepancyType.damaged,
        created_by_user_id=operator.id,
    )
    sibling_discrepancy = BoxRequestDiscrepancy(
        request_id=request.id,
        request_item_id=sibling_item.id,
        box_id=sibling.id,
        discrepancy_type=BoxRequestDiscrepancyType.damaged,
        created_by_user_id=operator.id,
    )
    session.add_all([selected_discrepancy, sibling_discrepancy])
    session.flush()
    selected_photo = _photo(
        session,
        discrepancy=selected_discrepancy,
        key="force/partial-selected.jpg",
        operator=operator,
    )
    shared_photo = _photo(
        session,
        discrepancy=selected_discrepancy,
        key="force/shared-partial.jpg",
        operator=operator,
    )
    document = BoxRequestDocument(
        request_id=request.id,
        document_type=BoxRequestDocumentType.other,
        erp_reference="KEEP",
        object_key="force/preserved-document.pdf",
        original_filename="keep.pdf",
        content_type="application/pdf",
        size_bytes=1,
        sha256="1" * 64,
        uploaded_by_user_id=operator.id,
    )
    attachment = BoxRequestAttachment(
        request_id=request.id,
        object_key=shared_photo.object_key,
        original_filename="keep.jpg",
        content_type="image/jpeg",
        size_bytes=1,
        sha256="2" * 64,
        uploaded_by_user_id=operator.id,
    )
    comment = BoxRequestComment(
        request_id=request.id,
        author_user_id=operator.id,
        body="must survive exactly",
    )
    exception = BoxRequestException(
        request_id=request.id,
        exception_kind=BoxRequestExceptionKind.hold,
        reason="retained operational history",
        resume_target=BoxRequestStatus.completed,
        created_by_user_id=operator.id,
    )
    notification = InAppNotification(
        user_id=operator.id,
        request_id=request.id,
        warehouse_id=request.warehouse_id,
        kind="force-test",
        title="Preserve",
        body="Preserve exactly",
        deep_link=f"/requests/{request.id}",
        idempotency_key=f"force-preserve-notification-{request.id}",
    )
    outbox = RequestEmailOutbox(
        request_id=request.id,
        recipient_user_id=operator.id,
        recipient_email=operator.email,
        kind="force-test",
        subject="Preserve",
        html_body="<p>Preserve</p>",
        text_body="Preserve",
        idempotency_key=f"force-preserve-email-{request.id}",
    )
    session.add_all(
        [document, attachment, comment, exception, notification, outbox]
    )
    session.commit()
    lot_id = selected.lot_id
    selected_box_id = selected.id
    selected_discrepancy_id = selected_discrepancy.id
    sibling_discrepancy_id = sibling_discrepancy.id
    selected_photo_id = selected_photo.id
    selected_photo_key = selected_photo.object_key
    shared_photo_id = shared_photo.id
    shared_photo_key = shared_photo.object_key
    selected_item_id = selected_item.id
    request_id = request.id
    sibling_item_id = sibling_item.id
    sibling_box_id = sibling.id
    preserved_artifacts = (
        (BoxRequestDocument, document.id, document.object_key),
        (BoxRequestAttachment, attachment.id, attachment.object_key),
        (BoxRequestComment, comment.id, comment.body),
        (BoxRequestException, exception.id, exception.reason),
        (InAppNotification, notification.id, notification.body),
        (RequestEmailOutbox, outbox.id, outbox.text_body),
    )

    preview = analyze_lot_force_purge_impact(session, lot_id=lot_id)
    assert preview.object_cleanup.deletable_keys == [selected_photo_key]
    assert preview.object_cleanup.shared_skipped_keys == [shared_photo_key]
    result = _execute_force_purge(
        session,
        admin=admin,
        preview=preview,
        reason="Remove the selected duplicate Lot graph",
    )
    session.expire_all()

    assert session.get(Lot, lot_id) is None
    assert session.get(Box, selected_box_id) is None
    assert session.get(Box, sibling.id) is not None
    preserved = session.get(BoxRequest, request_id)
    assert preserved is not None
    assert (
        preserved.quantity,
        preserved.actual_received_quantity,
        preserved.variance_quantity,
        preserved.status,
        preserved.version,
    ) == (1, 1, 0, BoxRequestStatus.completed, 2)
    assert [(item.id, item.position, item.box_id) for item in preserved.items] == [
        (sibling_item_id, 1, sibling_box_id)
    ]
    assert session.get(BoxRequestDiscrepancy, selected_discrepancy_id) is None
    assert session.get(BoxRequestDiscrepancyPhoto, selected_photo_id) is None
    assert session.get(BoxRequestDiscrepancyPhoto, shared_photo_id) is None
    assert session.get(BoxRequestDiscrepancy, sibling_discrepancy_id) is not None
    for model, entity_id, expected_value in preserved_artifacts:
        entity = session.get(model, entity_id)
        assert entity is not None
        actual_value = (
            entity.object_key
            if hasattr(entity, "object_key")
            else entity.body
            if hasattr(entity, "body")
            else entity.reason
            if hasattr(entity, "reason")
            else entity.text_body
        )
        assert actual_value == expected_value
    event = session.scalar(
        select(BoxRequestEvent)
        .where(
            BoxRequestEvent.request_id == request_id,
            BoxRequestEvent.event_type
            == BoxRequestEventType.force_purge_adjusted,
        )
        .order_by(BoxRequestEvent.id.desc())
    )
    assert event is not None
    assert event.event_metadata["purge_audit_id"] == result.audit_id
    assert event.event_metadata["rewrite"]["before"]["item_count"] == 2
    assert event.event_metadata["rewrite"]["after"]["item_count"] == 1
    audit = session.get(LotPurgeEvent, result.audit_id)
    assert audit is not None
    assert audit.event_metadata["purge_mode"] == "force"
    assert audit.object_keys == [selected_photo_key]
    assert audit.object_key_count == 1
    assert audit.event_metadata["deleted_counts"]["boxes"] == 1
    assert audit.event_metadata["request_rewrites"][0]["before"]["item_count"] == 2
    assert audit.event_metadata["request_rewrites"][0]["after"]["item_count"] == 1
    assert audit.event_metadata["request_rewrites"][0][
        "preserved_sibling_lot_ids"
    ] == [sibling.lot_id]
    assert selected_item_id in audit.event_metadata["deleted_entity_ids"][
        "box_request_items"
    ]
    assert selected_discrepancy_id in audit.event_metadata["deleted_entity_ids"][
        "box_request_discrepancies"
    ]
    assert selected_photo_id in audit.event_metadata["deleted_entity_ids"][
        "box_request_discrepancy_photos"
    ]
    assert shared_photo_id in audit.event_metadata["deleted_entity_ids"][
        "box_request_discrepancy_photos"
    ]
    assert audit.event_metadata["shared_skipped_object_keys"] == [shared_photo_key]

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
    assert cleanup.status.value == "completed"
    assert deleted_keys == [selected_photo_key]


def test_force_purge_deletes_full_graph_and_detaches_lineage(session, make_user):
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    selected = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Delete lineage source",
        warehouse_id=1,
    )
    deleted = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[selected],
        origin=BoxRequestOrigin.manual_entry,
        note="delete whole request",
    )
    sibling = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Preserved lineage request",
        warehouse_id=1,
    )
    preserved = _request(
        session,
        operator=operator,
        direction=BoxRequestDirection.return_,
        status=BoxRequestStatus.submitted,
        items=[sibling],
        source_id=deleted.id,
        parent_id=deleted.id,
        root_id=deleted.id,
    )
    shared_key = "force/shared-lineage.pdf"
    session.add_all(
        [
            BoxRequestDocument(
                request_id=deleted.id,
                document_type=BoxRequestDocumentType.other,
                erp_reference="DELETE",
                object_key=shared_key,
                original_filename="delete.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="d" * 64,
                uploaded_by_user_id=operator.id,
            ),
            BoxRequestAttachment(
                request_id=preserved.id,
                object_key=shared_key,
                original_filename="preserve.pdf",
                content_type="application/pdf",
                size_bytes=1,
                sha256="e" * 64,
                uploaded_by_user_id=operator.id,
            ),
        ]
    )
    session.commit()
    deleted_id = deleted.id
    preserved_id = preserved.id

    preview = analyze_lot_force_purge_impact(session, lot_id=selected.lot_id)
    result = _execute_force_purge(
        session,
        admin=admin,
        preview=preview,
        reason="Delete source and detach preserved lineage",
    )
    session.expire_all()

    assert session.get(BoxRequest, deleted_id) is None
    adjusted = session.get(BoxRequest, preserved_id)
    assert adjusted is not None
    assert (
        adjusted.source_inbound_request_id,
        adjusted.parent_request_id,
        adjusted.root_request_id,
        adjusted.version,
    ) == (None, None, None, 2)
    assert session.scalar(
        select(BoxRequestAttachment).where(
            BoxRequestAttachment.request_id == preserved_id,
            BoxRequestAttachment.object_key == shared_key,
        )
    ) is not None
    event = session.scalar(
        select(BoxRequestEvent).where(
            BoxRequestEvent.request_id == preserved_id,
            BoxRequestEvent.event_type
            == BoxRequestEventType.force_purge_adjusted,
        )
    )
    assert event is not None
    assert len(event.event_metadata["lineage_detachments"]) == 3
    audit = session.get(LotPurgeEvent, result.audit_id)
    assert audit is not None
    assert audit.object_keys == []
    assert audit.event_metadata["shared_skipped_object_keys"] == [shared_key]


def test_force_purge_preserves_all_mixed_request_shapes_and_lifecycle_timestamps(
    session,
    make_user,
):
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    selected = create_box(
        session,
        user=operator,
        box_number="1",
        lot="All request shapes selected",
        warehouse_id=1,
    )
    sibling = create_box(
        session,
        user=operator,
        box_number="1",
        lot="All request shapes sibling",
        warehouse_id=1,
    )
    specs = (
        (
            BoxRequestDirection.inbound,
            BoxRequestStatus.draft,
            BoxRequestOrigin.manual_entry,
        ),
        (
            BoxRequestDirection.inbound,
            BoxRequestStatus.submitted,
            BoxRequestOrigin.xlsx_import,
        ),
        (
            BoxRequestDirection.inbound,
            BoxRequestStatus.approved,
            BoxRequestOrigin.workflow,
        ),
        (
            BoxRequestDirection.inbound,
            BoxRequestStatus.preparing,
            BoxRequestOrigin.backorder,
        ),
        (
            BoxRequestDirection.inbound,
            BoxRequestStatus.completed,
            BoxRequestOrigin.manual_entry,
        ),
        (
            BoxRequestDirection.return_,
            BoxRequestStatus.approved,
            BoxRequestOrigin.workflow,
        ),
        (
            BoxRequestDirection.return_,
            BoxRequestStatus.completed,
            BoxRequestOrigin.return_reselection,
        ),
    )
    requests = []
    for direction, status, origin in specs:
        completed = status == BoxRequestStatus.completed
        request = _request(
            session,
            operator=operator,
            direction=direction,
            status=status,
            origin=origin,
            items=[selected, sibling],
            actual=2 if completed else None,
            variance=0 if completed else None,
        )
        if status in (BoxRequestStatus.approved, BoxRequestStatus.preparing):
            request.approved_at = datetime.now(UTC)
        requests.append(request)
    session.commit()
    request_ids = [request.id for request in requests]
    before = {
        request.id: {
            "status": request.status,
            "origin": request.origin,
            "direction": request.direction,
            "version": request.version,
            "created_at": request.created_at,
            "submitted_at": request.submitted_at,
            "approved_at": request.approved_at,
            "completed_at": request.completed_at,
            "updated_at": request.updated_at,
        }
        for request in requests
    }
    lot_id = selected.lot_id
    sibling_lot_id = sibling.lot_id
    sibling_box_id = sibling.id

    preview = analyze_lot_force_purge_impact(session, lot_id=lot_id)
    assert preview.request_rewrite_count == len(specs)
    result = _execute_force_purge(
        session,
        admin=admin,
        preview=preview,
        reason="Verify every mixed request lifecycle shape is preserved",
    )
    session.expire_all()

    assert session.get(Lot, sibling_lot_id) is not None
    assert session.get(Box, sibling_box_id) is not None
    for request_id in request_ids:
        preserved = session.get(BoxRequest, request_id)
        assert preserved is not None
        snapshot = before[request_id]
        assert preserved.status == snapshot["status"]
        assert preserved.origin == snapshot["origin"]
        assert preserved.direction == snapshot["direction"]
        assert preserved.version == snapshot["version"] + 1
        assert preserved.created_at == snapshot["created_at"]
        assert preserved.submitted_at == snapshot["submitted_at"]
        assert preserved.approved_at == snapshot["approved_at"]
        assert preserved.completed_at == snapshot["completed_at"]
        assert preserved.updated_at >= snapshot["updated_at"]
        assert [(item.position, item.box_id, item.lot_id) for item in preserved.items] == [
            (1, sibling_box_id, sibling_lot_id)
        ]
        assert (
            preserved.quantity,
            preserved.actual_received_quantity,
            preserved.variance_quantity,
        ) == (
            1,
            1 if preserved.status == BoxRequestStatus.completed else None,
            0 if preserved.status == BoxRequestStatus.completed else None,
        )
        adjustment = session.scalar(
            select(BoxRequestEvent).where(
                BoxRequestEvent.request_id == request_id,
                BoxRequestEvent.event_type
                == BoxRequestEventType.force_purge_adjusted,
            )
        )
        assert adjustment is not None
        assert adjustment.from_status == preserved.status
        assert adjustment.to_status == preserved.status

    audit = session.get(LotPurgeEvent, result.audit_id)
    assert audit is not None
    assert {
        rewrite["request_id"] for rewrite in audit.event_metadata["request_rewrites"]
    } == set(request_ids)


def test_force_purge_hybrid_item_never_deletes_sibling_box(session, make_user):
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    selected = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Hybrid selected",
        warehouse_id=1,
    )
    sibling = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Hybrid sibling",
        warehouse_id=1,
    )
    request = _request(
        session,
        operator=operator,
        direction=BoxRequestDirection.inbound,
        status=BoxRequestStatus.submitted,
        items=[selected, sibling],
    )
    hybrid_item, sibling_item = sorted(request.items, key=lambda item: item.position)
    hybrid_item.box_id = sibling.id
    session.commit()
    sibling_box_id = sibling.id
    sibling_lot_id = sibling.lot_id

    preview = analyze_lot_force_purge_impact(session, lot_id=selected.lot_id)
    rewrite = preview.request_rewrites[0]
    assert rewrite.removed_item_ids == [hybrid_item.id]
    _execute_force_purge(
        session,
        admin=admin,
        preview=preview,
        reason="Remove inconsistent selected Lot provenance only",
    )
    session.expire_all()

    assert session.get(Box, sibling_box_id) is not None
    assert session.get(Lot, sibling_lot_id) is not None
    preserved = session.get(BoxRequest, request.id)
    assert preserved is not None
    assert [(item.id, item.position, item.box_id) for item in preserved.items] == [
        (sibling_item.id, 1, sibling_box_id)
    ]


def test_force_purge_rollback_restores_audit_and_data(
    session,
    make_user,
    monkeypatch,
):
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    selected = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Force rollback selected",
        warehouse_id=1,
    )
    sibling = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Force rollback sibling",
        warehouse_id=1,
    )
    request = _request(
        session,
        operator=operator,
        direction=BoxRequestDirection.inbound,
        status=BoxRequestStatus.completed,
        items=[selected, sibling],
        actual=2,
        variance=0,
    )
    session.commit()
    preview = analyze_lot_force_purge_impact(session, lot_id=selected.lot_id)
    selected_lot_id = selected.lot_id
    selected_box_id = selected.id
    request_id = request.id
    item_snapshot = [
        (item.id, item.position, item.box_id, item.lot_id) for item in request.items
    ]

    def fail_commit():
        raise RuntimeError("forced commit failure")

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="forced commit failure"):
        _execute_force_purge(
            session,
            admin=admin,
            preview=preview,
            reason="Prove atomic rollback of force purge data",
        )

    session.expire_all()
    assert session.get(Lot, selected_lot_id) is not None
    assert session.get(Box, selected_box_id) is not None
    preserved = session.get(BoxRequest, request_id)
    assert preserved is not None
    assert preserved.version == 1
    assert [
        (item.id, item.position, item.box_id, item.lot_id)
        for item in preserved.items
    ] == item_snapshot
    assert session.scalar(select(LotPurgeEvent)) is None
    assert session.scalar(
        select(BoxRequestEvent).where(
            BoxRequestEvent.request_id == request_id,
            BoxRequestEvent.event_type == BoxRequestEventType.force_purge_adjusted,
        )
    ) is None


def test_force_purge_api_confirmations_admin_gate_and_sse(
    client,
    session,
    make_user,
    monkeypatch,
):
    operator = make_user(UserRole.operator)
    selected = create_box(
        session,
        user=operator,
        box_number="1",
        lot="API force purge",
        warehouse_id=1,
    )
    create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[selected],
        origin=BoxRequestOrigin.manual_entry,
        note="api execution",
    )
    session.commit()
    preview_response = client.get(
        f"/api/lots/{selected.lot_id}/force-purge-preview"
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert "deletable_keys" not in str(preview)
    acknowledgements = [
        blocker["code"] for blocker in preview["overridden_blockers"]
    ]
    payload = {
        "confirmation_name": preview["lot_name"],
        "confirmation_phrase": preview["confirmation_phrase"],
        "reason": "A sufficiently detailed administrative reason",
        "expected_version": preview["lot_version"],
        "expected_graph_signature": preview["graph_signature"],
        "acknowledged_blocker_codes": acknowledgements,
    }
    for field, value, code in (
        ("confirmation_name", preview["lot_name"].lower(), "confirmation_name_mismatch"),
        ("confirmation_phrase", "FORCE DELETE LOT 0", "confirmation_phrase_mismatch"),
        ("expected_version", preview["lot_version"] + 1, "version_conflict"),
        ("expected_graph_signature", "0" * 64, "graph_changed"),
        ("acknowledged_blocker_codes", [], "acknowledgement_mismatch"),
    ):
        invalid = {**payload, field: value}
        response = client.post(
            f"/api/lots/{selected.lot_id}/force-purge",
            json=invalid,
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == code
        assert response.json()["detail"]["current_preview"] is not None
        session.rollback()

    published: list[tuple[str, dict[str, object]]] = []

    async def capture(event_type, data):
        published.append((event_type, data))

    monkeypatch.setattr(bus, "publish", capture)
    response = client.post(
        f"/api/lots/{selected.lot_id}/force-purge",
        json=payload,
    )
    assert response.status_code == 200
    assert response.json()["purge_mode"] == "force"
    assert response.json()["object_cleanup_status"] == "not_required"
    assert published
    assert all(event_type == "lot.purged" for event_type, _data in published)
    assert all(data["purge_mode"] == "force" for _event_type, data in published)
    assert all("reason" not in data for _event_type, data in published)

    viewer = make_user(UserRole.viewer)
    app.dependency_overrides[get_current_user] = lambda: viewer
    other = get_or_create_lot(
        session,
        user=operator,
        name="Admin gated force purge",
        warehouse_id=1,
    )
    session.commit()
    assert (
        client.get(f"/api/lots/{other.id}/force-purge-preview").status_code
        == 403
    )


def test_force_purge_cleanup_failure_and_retry(
    client,
    session,
    make_user,
    monkeypatch,
):
    operator = make_user(UserRole.operator)
    selected = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Force cleanup retry",
        warehouse_id=1,
    )
    request = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[selected],
        origin=BoxRequestOrigin.manual_entry,
        note="cleanup retry",
    )
    object_key = "force/full-graph-document.pdf"
    session.add(
        BoxRequestDocument(
            request_id=request.id,
            document_type=BoxRequestDocumentType.other,
            erp_reference="DELETE",
            object_key=object_key,
            original_filename="delete.pdf",
            content_type="application/pdf",
            size_bytes=1,
            sha256="f" * 64,
            uploaded_by_user_id=operator.id,
        )
    )
    session.commit()
    preview = client.get(
        f"/api/lots/{selected.lot_id}/force-purge-preview"
    ).json()
    monkeypatch.setattr(
        lots_service,
        "delete_document_strict",
        lambda _key: (_ for _ in ()).throw(RuntimeError("storage down")),
    )
    response = client.post(
        f"/api/lots/{selected.lot_id}/force-purge",
        json={
            "confirmation_name": preview["lot_name"],
            "confirmation_phrase": preview["confirmation_phrase"],
            "reason": "Delete duplicate data and retry storage cleanup",
            "expected_version": preview["lot_version"],
            "expected_graph_signature": preview["graph_signature"],
            "acknowledged_blocker_codes": [
                blocker["code"] for blocker in preview["overridden_blockers"]
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object_cleanup_status"] == "failed"
    assert body["object_cleanup_failure_count"] == 1
    audit = session.get(LotPurgeEvent, body["purge_audit_id"])
    assert audit is not None
    assert audit.object_keys == [object_key]
    assert audit.event_metadata["operation"] == "lot_force_purge"

    deleted_keys: list[str] = []
    monkeypatch.setattr(
        lots_service,
        "delete_document_strict",
        lambda key: deleted_keys.append(key),
    )
    retry = client.post(
        f"/api/lots/purge-audits/{body['purge_audit_id']}/cleanup-retry"
    )
    assert retry.status_code == 200
    assert retry.json()["object_cleanup_status"] == "completed"
    assert deleted_keys == [object_key]
