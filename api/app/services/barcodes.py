from __future__ import annotations

import re
import threading
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, case, func, select, text, tuple_
from sqlalchemy.orm import Session

from app.models.barcode_identities import BarcodeEntityKind, BarcodeIdentity
from app.models.users import User, UserRole

if TYPE_CHECKING:
    from app.models.box_files import BoxFile
    from app.models.boxes import Box
    from app.models.lots import Lot
    from app.models.pallets import Pallet


MAX_ISSUANCE_NUMBER = 999_999_999_999
BARCODE_PATTERN = re.compile(r"^(LOT|PAL|BOX|FIL)-(\d{12})-(\d)$")
PREFIX_BY_KIND = {
    BarcodeEntityKind.lot: "LOT",
    BarcodeEntityKind.pallet: "PAL",
    BarcodeEntityKind.box: "BOX",
    BarcodeEntityKind.file: "FIL",
}
KIND_LOCK_ORDER = {
    BarcodeEntityKind.lot: 0,
    BarcodeEntityKind.pallet: 1,
    BarcodeEntityKind.box: 2,
    BarcodeEntityKind.file: 3,
}
_SQLITE_ISSUANCE_LOCK = threading.Lock()
_SQLITE_NEXT_NUMBER: weakref.WeakKeyDictionary[object, int] = (
    weakref.WeakKeyDictionary()
)


@dataclass(frozen=True)
class BarcodeRetirementTarget:
    entity_kind: BarcodeEntityKind
    entity_id: int
    hierarchy: dict[str, object]


def mod10_check_digit(number: int | str) -> int:
    """Return the GS1 Mod10 digit for a twelve-digit issuance number."""
    digits = str(number)
    if not digits.isdigit() or len(digits) > 12:
        raise ValueError("issuance number must contain at most 12 digits")
    digits = digits.zfill(12)
    weighted_sum = sum(
        int(digit) * (3 if offset % 2 == 0 else 1)
        for offset, digit in enumerate(reversed(digits))
    )
    return (10 - weighted_sum % 10) % 10


def format_barcode(kind: BarcodeEntityKind | str, issuance_number: int) -> str:
    """Build the one canonical barcode representation."""
    kind = BarcodeEntityKind(kind)
    if not 1 <= issuance_number <= MAX_ISSUANCE_NUMBER:
        raise ValueError("issuance number is outside the 12-digit namespace")
    body = f"{issuance_number:012d}"
    return f"{PREFIX_BY_KIND[kind]}-{body}-{mod10_check_digit(body)}"


def parse_barcode(value: str) -> tuple[BarcodeEntityKind, int]:
    """Validate a canonical barcode and return its kind and issuance number."""
    match = BARCODE_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("barcode does not match the canonical format")
    prefix, number_text, check_text = match.groups()
    kind = next(kind for kind, candidate in PREFIX_BY_KIND.items() if candidate == prefix)
    number = int(number_text)
    if number == 0 or int(check_text) != mod10_check_digit(number_text):
        raise ValueError("barcode has an invalid issuance number or check digit")
    return kind, number


def is_valid_barcode(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parse_barcode(value)
    except ValueError:
        return False
    return True


def _entity_details(entity: object) -> tuple[BarcodeEntityKind, type[Any], str]:
    # Imports stay local so model modules can expose their read-only properties.
    from app.models.box_files import BoxFile
    from app.models.boxes import Box
    from app.models.lots import Lot
    from app.models.pallets import Pallet

    details: tuple[tuple[type[Any], BarcodeEntityKind, str], ...] = (
        (Lot, BarcodeEntityKind.lot, "lots"),
        (Pallet, BarcodeEntityKind.pallet, "pallets"),
        (Box, BarcodeEntityKind.box, "boxes"),
        (BoxFile, BarcodeEntityKind.file, "box_files"),
    )
    for model, kind, table_name in details:
        if isinstance(entity, model):
            return kind, model, table_name
    raise TypeError(f"{type(entity).__name__} does not receive a system barcode")


def reserve_issuance_number(session: Session) -> int:
    """Atomically reserve the next number in the database's shared namespace."""
    connection = session.connection()
    if connection.dialect.name == "postgresql":
        number = connection.scalar(text("SELECT nextval('barcode_identity_number_seq')"))
    elif connection.dialect.name == "sqlite":
        # The UPDATE obtains SQLite's database write lock before returning the
        # number. The process-local high-water mark intentionally survives a
        # transaction rollback so an observed finalized value is never reused.
        with _SQLITE_ISSUANCE_LOCK:
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO barcode_issuance_counter "
                    "(singleton_id, next_number) VALUES (1, 1)"
                )
            )
            engine = connection.engine
            next_number = _SQLITE_NEXT_NUMBER.get(engine)
            if next_number is None:
                database_next = connection.scalar(
                    text(
                        "SELECT next_number FROM barcode_issuance_counter "
                        "WHERE singleton_id = 1"
                    )
                )
                registry_next = connection.scalar(
                    select(func.coalesce(func.max(BarcodeIdentity.issuance_number), 0) + 1)
                )
                next_number = max(int(database_next or 1), int(registry_next or 1))
            number = next_number
            _SQLITE_NEXT_NUMBER[engine] = number + 1
            connection.execute(
                text(
                    "UPDATE barcode_issuance_counter "
                    "SET next_number = CASE "
                    "WHEN next_number < :next_number THEN :next_number "
                    "ELSE next_number END WHERE singleton_id = 1"
                ),
                {"next_number": number + 1},
            )
    else:
        raise RuntimeError(f"barcode issuance is unsupported on {connection.dialect.name}")
    if not isinstance(number, int) or number > MAX_ISSUANCE_NUMBER:
        raise RuntimeError("the shared barcode namespace is exhausted")
    return number


def _reserve_entity_id(
    session: Session,
    model: type[Any],
    table_name: str,
    kind: BarcodeEntityKind,
) -> int:
    connection = session.connection()
    if connection.dialect.name == "postgresql":
        sequence = connection.scalar(
            text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
            {"table_name": table_name},
        )
        if not sequence:
            raise RuntimeError(f"{table_name}.id has no PostgreSQL sequence")
        entity_id = connection.scalar(
            text("SELECT nextval(CAST(:sequence AS regclass))"),
            {"sequence": sequence},
        )
    else:
        live_max = connection.scalar(select(func.coalesce(func.max(model.id), 0)))
        historical_max = connection.scalar(
            select(func.coalesce(func.max(BarcodeIdentity.entity_id), 0)).where(
                BarcodeIdentity.entity_kind == kind
            )
        )
        pending_max = max(
            (
                int(item.id)
                for item in session.new
                if isinstance(item, model) and getattr(item, "id", None) is not None
            ),
            default=0,
        )
        entity_id = max(
            int(live_max or 0),
            int(historical_max or 0),
            pending_max,
        ) + 1
    if not isinstance(entity_id, int):
        raise RuntimeError(f"could not reserve an ID for {table_name}")
    return entity_id


def issue_barcode_identity(
    session: Session,
    entity: Lot | Pallet | Box | BoxFile,
    *,
    actor_user_id: int | None = None,
    reason: str | None = None,
    metadata: dict[str, object] | None = None,
) -> BarcodeIdentity:
    """Create or recover the one finalized identity for an entity."""
    kind, model, table_name = _entity_details(entity)
    existing_id = getattr(entity, "barcode_identity_id", None)
    linked_identity = getattr(entity, "barcode_identity", None)
    if linked_identity is not None or existing_id is not None:
        identity = linked_identity or session.get(BarcodeIdentity, existing_id)
        if identity is None:
            raise ValueError("entity references a missing barcode identity")
        finalize_barcode_identity(identity, entity)
        return identity

    if getattr(entity, "id", None) is None:
        entity.id = _reserve_entity_id(session, model, table_name, kind)
    else:
        # Compatibility/restoration paths can expose a persisted legacy row
        # whose link is still NULL. Lock and recover a registry row first so
        # racing repair workers cannot issue two identities.
        persisted = session.scalar(
            select(model)
            .where(model.id == entity.id)
            .with_for_update(of=model)
            .execution_options(populate_existing=True)
        )
        if persisted is not None:
            existing = session.scalar(
                select(BarcodeIdentity)
                .where(
                    BarcodeIdentity.entity_kind == kind,
                    BarcodeIdentity.entity_id == entity.id,
                )
                .with_for_update()
            )
            if existing is not None:
                finalize_barcode_identity(existing, entity)
                return existing

    number = reserve_issuance_number(session)
    return _build_identity(
        session,
        entity,
        kind=kind,
        number=number,
        actor_user_id=actor_user_id,
        reason=reason,
        metadata=metadata,
    )


def _build_identity(
    session: Session,
    entity: Lot | Pallet | Box | BoxFile,
    *,
    kind: BarcodeEntityKind,
    number: int,
    actor_user_id: int | None,
    reason: str | None,
    metadata: dict[str, object] | None,
) -> BarcodeIdentity:
    identity = BarcodeIdentity(
        id=number,
        entity_kind=kind,
        issuance_number=number,
        barcode=format_barcode(kind, number),
        entity_id=entity.id,
        issued_by_user_id=actor_user_id,
        issuance_reason=reason,
        issuance_metadata=metadata or {},
    )
    finalize_barcode_identity(identity, entity)
    session.add(identity)
    return identity


def finalize_barcode_identity(
    identity: BarcodeIdentity,
    entity: Lot | Pallet | Box | BoxFile,
) -> None:
    """Validate and establish both finalized sides of an identity link."""
    kind, _, _ = _entity_details(entity)
    entity_id = getattr(entity, "id", None)
    if entity_id is None:
        raise ValueError("entity ID must be reserved before finalization")
    if identity.entity_kind != kind or identity.entity_id != entity_id:
        raise ValueError("identity kind/entity does not match the target")
    if identity.id != identity.issuance_number:
        raise ValueError("identity ID must equal its issuance number")
    if identity.barcode != format_barcode(kind, identity.issuance_number):
        raise ValueError("identity barcode is not canonical")
    existing = getattr(entity, "barcode_identity_id", None)
    if existing is not None and existing != identity.id:
        raise ValueError("entity already has another barcode identity")
    entity.barcode_identity = identity


def issue_pending_barcode_identities(
    session: Session,
    *,
    actor_user_id: int | None = None,
    reason: str = "automatic session issuance",
) -> None:
    """Issue identities for pending entities (useful for controlled session hooks)."""
    pending: list[tuple[str, int, object]] = []
    kind_order = {"lot": 0, "pallet": 1, "box": 2, "file": 3}
    for ordinal, entity in enumerate(tuple(session.new)):
        try:
            kind, _, _ = _entity_details(entity)
        except TypeError:
            continue
        if (
            getattr(entity, "barcode_identity_id", None) is None
            and getattr(entity, "barcode_identity", None) is None
        ):
            pending.append((kind.value, ordinal, entity))
    next_entity_ids: dict[type[Any], int] = {}
    for _, _, entity in sorted(pending, key=lambda item: (kind_order[item[0]], item[1])):
        kind, model, table_name = _entity_details(entity)
        number = reserve_issuance_number(session)
        if getattr(entity, "id", None) is None:
            if model not in next_entity_ids:
                next_entity_ids[model] = _reserve_entity_id(
                    session,
                    model,
                    table_name,
                    kind,
                )
            entity.id = next_entity_ids[model]
            next_entity_ids[model] += 1
        _build_identity(
            session,
            entity,
            kind=kind,
            number=number,
            actor_user_id=actor_user_id,
            reason=reason,
            metadata=None,
        )


def _kind_for_id_key(key: str) -> BarcodeEntityKind | None:
    singular = {
        "lot": BarcodeEntityKind.lot,
        "pallet": BarcodeEntityKind.pallet,
        "box": BarcodeEntityKind.box,
        "file": BarcodeEntityKind.file,
    }
    tokens = key.removesuffix("_id").split("_")
    return next((singular[token] for token in reversed(tokens) if token in singular), None)


def _collect_barcode_targets(
    value: object,
    targets: set[tuple[BarcodeEntityKind, int]],
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                isinstance(key, str)
                and key.endswith("_id")
                and isinstance(item, int)
                and (kind := _kind_for_id_key(key)) is not None
            ):
                targets.add((kind, item))
            _collect_barcode_targets(item, targets)
    elif isinstance(value, list):
        for item in value:
            _collect_barcode_targets(item, targets)


def _barcode_map(
    session: Session,
    targets: set[tuple[BarcodeEntityKind, int]],
) -> dict[tuple[BarcodeEntityKind, int], str]:
    result = {
        (identity.entity_kind, identity.entity_id): identity.barcode
        for identity in session.new
        if isinstance(identity, BarcodeIdentity)
    }
    missing = targets - result.keys()
    if missing:
        rows = session.connection().execute(
            select(
                BarcodeIdentity.entity_kind,
                BarcodeIdentity.entity_id,
                BarcodeIdentity.barcode,
            ).where(
                tuple_(
                    BarcodeIdentity.entity_kind,
                    BarcodeIdentity.entity_id,
                ).in_(sorted(missing, key=lambda item: (item[0].value, item[1])))
            )
        )
        result.update(
            ((BarcodeEntityKind(kind), int(entity_id)), barcode)
            for kind, entity_id, barcode in rows
        )
    return result


def _enrich_barcode_dict(
    value: dict[str, object],
    barcodes: dict[tuple[BarcodeEntityKind, int], str],
    *,
    default_kind: BarcodeEntityKind | None = None,
) -> dict[str, object]:
    enriched = dict(value)
    if default_kind is not None and isinstance(enriched.get("id"), int):
        barcode = barcodes.get((default_kind, int(enriched["id"])))
        if barcode is not None:
            enriched.setdefault(f"{default_kind.value}_barcode", barcode)
            enriched.setdefault("barcode", barcode)
    for key, item in list(enriched.items()):
        if isinstance(item, dict):
            enriched[key] = _enrich_barcode_dict(item, barcodes)
        elif isinstance(item, list):
            enriched[key] = [
                _enrich_barcode_dict(candidate, barcodes)
                if isinstance(candidate, dict)
                else candidate
                for candidate in item
            ]
        if (
            key.endswith("_id")
            and isinstance(item, int)
            and (kind := _kind_for_id_key(key)) is not None
        ):
            barcode = barcodes.get((kind, item))
            if barcode is not None:
                enriched.setdefault(f"{key.removesuffix('_id')}_barcode", barcode)
    return enriched


def preserve_pending_barcode_snapshots(session: Session) -> None:
    """Fill write-once request snapshots and self-contained event evidence."""
    from app.models.box_files import BoxFileEvent
    from app.models.boxes import BoxEvent
    from app.models.lots import LotEvent
    from app.models.pallets import PalletEvent
    from app.models.requests import BoxRequestItem, BoxRequestItemFileSnapshot

    event_kinds = {
        LotEvent: BarcodeEntityKind.lot,
        PalletEvent: BarcodeEntityKind.pallet,
        BoxEvent: BarcodeEntityKind.box,
        BoxFileEvent: BarcodeEntityKind.file,
    }
    candidates = tuple(session.new) + tuple(session.dirty)
    targets: set[tuple[BarcodeEntityKind, int]] = set()
    for candidate in candidates:
        if isinstance(candidate, BoxRequestItem):
            for kind, entity_id in (
                (BarcodeEntityKind.lot, candidate.lot_id),
                (BarcodeEntityKind.pallet, candidate.pallet_id),
                (BarcodeEntityKind.box, candidate.box_id),
            ):
                if entity_id is not None:
                    targets.add((kind, entity_id))
        elif isinstance(candidate, BoxRequestItemFileSnapshot):
            if candidate.file_id is not None:
                targets.add((BarcodeEntityKind.file, candidate.file_id))
        elif (kind := event_kinds.get(type(candidate))) is not None:
            entity_id = getattr(candidate, f"{kind.value}_id")
            targets.add((kind, entity_id))
            metadata = candidate.event_metadata or {}
            _collect_barcode_targets(metadata, targets)
            if isinstance(candidate, BoxFileEvent):
                for snapshot in (
                    candidate.before_snapshot,
                    candidate.after_snapshot,
                ):
                    if snapshot:
                        targets.add((BarcodeEntityKind.file, candidate.file_id))
                        _collect_barcode_targets(snapshot, targets)
    barcodes = _barcode_map(session, targets)
    for candidate in candidates:
        if isinstance(candidate, BoxRequestItem):
            for kind, entity_id, attribute in (
                (BarcodeEntityKind.lot, candidate.lot_id, "lot_barcode"),
                (BarcodeEntityKind.pallet, candidate.pallet_id, "pallet_barcode"),
                (BarcodeEntityKind.box, candidate.box_id, "box_barcode"),
            ):
                if getattr(candidate, attribute) is None and entity_id is not None:
                    barcode = barcodes.get((kind, entity_id))
                    if barcode is not None:
                        setattr(candidate, attribute, barcode)
        elif isinstance(candidate, BoxRequestItemFileSnapshot):
            if candidate.barcode is None and candidate.file_id is not None:
                barcode = barcodes.get((BarcodeEntityKind.file, candidate.file_id))
                if barcode is not None:
                    candidate.barcode = barcode
        elif (kind := event_kinds.get(type(candidate))) is not None:
            entity_id = getattr(candidate, f"{kind.value}_id")
            metadata = _enrich_barcode_dict(candidate.event_metadata or {}, barcodes)
            barcode = barcodes.get((kind, entity_id))
            if barcode is not None:
                metadata.setdefault(f"{kind.value}_barcode", barcode)
            candidate.event_metadata = metadata
            if isinstance(candidate, BoxFileEvent):
                if candidate.before_snapshot is not None:
                    candidate.before_snapshot = _enrich_barcode_dict(
                        candidate.before_snapshot,
                        barcodes,
                        default_kind=BarcodeEntityKind.file,
                    )
                if candidate.after_snapshot is not None:
                    candidate.after_snapshot = _enrich_barcode_dict(
                        candidate.after_snapshot,
                        barcodes,
                        default_kind=BarcodeEntityKind.file,
                    )


def retire_barcode_identity(
    identity: BarcodeIdentity,
    *,
    actor_user_id: int | None,
    reason: str,
    metadata: dict[str, object] | None = None,
    retired_at: datetime | None = None,
) -> None:
    """Apply the one allowed lifecycle transition to a registry record."""
    if identity.retired_at is not None:
        raise ValueError("barcode identity is already retired")
    if not reason.strip():
        raise ValueError("retirement reason must not be blank")
    identity.retired_at = retired_at or datetime.now(UTC)
    identity.retired_by_user_id = actor_user_id
    identity.retirement_reason = reason.strip()
    identity.retirement_metadata = metadata or {}


def barcode_retirement_target(
    entity: Lot | Pallet | Box | BoxFile,
    *,
    hierarchy: dict[str, object] | None = None,
) -> BarcodeRetirementTarget:
    """Capture stable lookup context before a live entity is hard-deleted."""
    kind, _, _ = _entity_details(entity)
    entity_id = getattr(entity, "id", None)
    if entity_id is None:
        raise ValueError("cannot retire a barcode for an entity without an ID")
    if hierarchy is None:
        if kind == BarcodeEntityKind.lot:
            hierarchy = {
                "lot_id": entity.id,
                "lot_barcode": entity.barcode,
                "lot_name": entity.name,
            }
        elif kind == BarcodeEntityKind.pallet:
            hierarchy = {
                "lot_id": entity.lot_id,
                "lot_barcode": entity.lot.barcode,
                "pallet_id": entity.id,
                "pallet_barcode": entity.barcode,
                "pallet_number": entity.pallet_number,
            }
        elif kind == BarcodeEntityKind.box:
            hierarchy = {
                "lot_id": entity.lot_id,
                "lot_barcode": entity.lot_record.barcode,
                "box_id": entity.id,
                "box_barcode": entity.barcode,
                "box_number": entity.box_number,
                "pallet_id": entity.pallet_id,
                "pallet_barcode": (
                    entity.pallet.barcode if entity.pallet is not None else None
                ),
                "warehouse_id": entity.current_warehouse_id,
            }
        else:
            hierarchy = {
                "lot_id": entity.lot_id,
                "lot_barcode": entity.lot.barcode,
                "box_id": entity.box_id,
                "box_barcode": entity.box.barcode,
                "file_id": entity.id,
                "file_barcode": entity.barcode,
                "file_reference": entity.reference,
            }
    return BarcodeRetirementTarget(kind, int(entity_id), hierarchy)


def barcode_identity_lock_statement(
    targets: list[BarcodeRetirementTarget],
) -> Select[tuple[BarcodeIdentity]]:
    """Build the shared deterministic registry lock statement."""
    pairs = [(target.entity_kind, target.entity_id) for target in targets]
    lock_order = case(
        *[
            (BarcodeIdentity.entity_kind == kind, ordinal)
            for kind, ordinal in KIND_LOCK_ORDER.items()
        ]
    )
    return (
        select(BarcodeIdentity)
        .where(
            tuple_(
                BarcodeIdentity.entity_kind,
                BarcodeIdentity.entity_id,
            ).in_(pairs)
        )
        .order_by(lock_order, BarcodeIdentity.entity_id)
        .with_for_update(of=BarcodeIdentity)
    )


def retire_barcode_identities(
    session: Session,
    targets: list[BarcodeRetirementTarget],
    *,
    actor_user_id: int | None,
    reason: str,
    operation_metadata: dict[str, object],
    retired_at: datetime | None = None,
) -> list[BarcodeIdentity]:
    """Lock and retire a destructive batch without per-entity queries."""
    if not targets:
        return []
    if not reason.strip():
        raise ValueError("retirement reason must not be blank")
    unique_targets = {
        (target.entity_kind, target.entity_id): target for target in targets
    }
    ordered_targets = sorted(
        unique_targets.values(),
        key=lambda item: (KIND_LOCK_ORDER[item.entity_kind], item.entity_id),
    )
    identities = list(
        session.scalars(barcode_identity_lock_statement(ordered_targets)).all()
    )
    by_key = {
        (identity.entity_kind, identity.entity_id): identity
        for identity in identities
    }
    missing = [
        f"{target.entity_kind.value}:{target.entity_id}"
        for target in ordered_targets
        if (target.entity_kind, target.entity_id) not in by_key
    ]
    if missing:
        raise ValueError(f"missing barcode identities for {', '.join(missing)}")

    timestamp = retired_at or datetime.now(UTC)
    for target in ordered_targets:
        identity = by_key[(target.entity_kind, target.entity_id)]
        if identity.retired_at is not None:
            raise ValueError(
                f"barcode identity is already retired for "
                f"{target.entity_kind.value}:{target.entity_id}"
            )
        retire_barcode_identity(
            identity,
            actor_user_id=actor_user_id,
            reason=reason,
            retired_at=timestamp,
            metadata={
                **operation_metadata,
                "entity_kind": target.entity_kind.value,
                "entity_id": target.entity_id,
                "hierarchy": target.hierarchy,
            },
        )
    # Destructive callers use Core DELETE statements with autoflush disabled.
    # Persist retirement first so database deletion guards observe it.
    session.flush()
    return identities


class BarcodeLookupError(ValueError):
    """Base class for public barcode resolution failures."""


class InvalidBarcodeError(BarcodeLookupError):
    pass


class BarcodeNotFoundError(BarcodeLookupError):
    pass


def _redirect(
    entity: Lot | Pallet,
    kind: BarcodeEntityKind,
) -> dict[str, object]:
    entity_id = entity.id
    barcode = entity.barcode
    label = (
        f"Lot {entity.name}"
        if kind == BarcodeEntityKind.lot
        else f"Pallet {entity.pallet_number}"
    )
    return {
        "entity_kind": kind.value,
        "entity_id": entity_id,
        "barcode": barcode,
        "frontend_path": f"/{kind.value}s/{entity_id}",
        "display_label": label,
    }


def _retired_result(identity: BarcodeIdentity) -> dict[str, object]:
    metadata = identity.retirement_metadata or {}
    hierarchy = metadata.get("hierarchy")
    return {
        "entity_kind": identity.entity_kind.value,
        "entity_id": identity.entity_id,
        "barcode": identity.barcode,
        "lifecycle_state": "retired",
        "retired": True,
        "frontend_path": None,
        "display_label": (
            f"Retired {identity.entity_kind.value.title()} #{identity.entity_id}"
        ),
        "hierarchy": hierarchy if isinstance(hierarchy, dict) else {},
        "redirect": None,
        "retired_at": identity.retired_at,
        "retirement_reason": identity.retirement_reason,
        "retirement_operation": metadata.get("operation"),
        "retirement_metadata": metadata,
    }


def resolve_barcode(
    db: Session,
    *,
    user: User,
    barcode: str,
) -> dict[str, object]:
    """Resolve one exact canonical identity without making 404s enumerable."""
    try:
        kind, issuance_number = parse_barcode(barcode)
    except ValueError as exc:
        raise InvalidBarcodeError(str(exc)) from exc

    identity = db.scalar(
        select(BarcodeIdentity).where(
            BarcodeIdentity.entity_kind == kind,
            BarcodeIdentity.issuance_number == issuance_number,
            BarcodeIdentity.barcode == barcode,
        )
    )
    if identity is None:
        raise BarcodeNotFoundError("barcode not found")
    if identity.retired_at is not None:
        if user.role != UserRole.admin:
            raise BarcodeNotFoundError("barcode not found")
        return _retired_result(identity)

    # Local imports avoid cycles with creation services that issue identities.
    from app.models.box_files import BoxFile
    from app.models.boxes import Box, BoxStatus
    from app.models.lots import Lot
    from app.models.pallets import Pallet
    from app.services.acl import can_access
    from app.services.lots import LotRuleError, get_visible_lot_summary
    from app.services.pallets import (
        PalletRuleError,
        get_visible_pallet_summary,
    )

    if kind == BarcodeEntityKind.lot:
        lot = db.get(Lot, identity.entity_id)
        if lot is None:
            raise BarcodeNotFoundError("barcode not found")
        redirect = None
        visible_lot = lot
        lifecycle = "active"
        if lot.merged_into_lot_id is not None:
            lifecycle = "merged"
            visited = {lot.id}
            while visible_lot.merged_into_lot_id is not None:
                if visible_lot.merged_into_lot_id in visited:
                    raise BarcodeNotFoundError("barcode not found")
                visited.add(visible_lot.merged_into_lot_id)
                candidate = db.get(Lot, visible_lot.merged_into_lot_id)
                if candidate is None:
                    raise BarcodeNotFoundError("barcode not found")
                visible_lot = candidate
            redirect = _redirect(visible_lot, BarcodeEntityKind.lot)
        try:
            summary = get_visible_lot_summary(
                db, user=user, lot_id=visible_lot.id
            )
        except LotRuleError as exc:
            raise BarcodeNotFoundError("barcode not found") from exc
        return {
            "entity_kind": kind.value,
            "entity_id": lot.id,
            "barcode": identity.barcode,
            "lifecycle_state": lifecycle,
            "retired": False,
            "frontend_path": f"/lots/{lot.id}",
            "display_label": f"Lot {lot.name}",
            "hierarchy": {
                "lot": {"id": lot.id, "barcode": lot.barcode, "name": lot.name},
                "visible_warehouse_names": summary.warehouse_names,
            },
            "redirect": redirect,
            "retired_at": None,
            "retirement_reason": None,
            "retirement_operation": None,
            "retirement_metadata": None,
        }

    if kind == BarcodeEntityKind.pallet:
        pallet = db.get(Pallet, identity.entity_id)
        if pallet is None:
            raise BarcodeNotFoundError("barcode not found")
        redirect = None
        visible_pallet = pallet
        if pallet.absorbed_into_pallet_id is not None:
            lifecycle = "absorbed"
            visited = {pallet.id}
            while visible_pallet.absorbed_into_pallet_id is not None:
                if visible_pallet.absorbed_into_pallet_id in visited:
                    raise BarcodeNotFoundError("barcode not found")
                visited.add(visible_pallet.absorbed_into_pallet_id)
                candidate = db.get(Pallet, visible_pallet.absorbed_into_pallet_id)
                if candidate is None:
                    raise BarcodeNotFoundError("barcode not found")
                visible_pallet = candidate
            redirect = _redirect(visible_pallet, BarcodeEntityKind.pallet)
            include_inactive = False
        else:
            lifecycle = "active" if pallet.is_active else "archived"
            include_inactive = not pallet.is_active
        try:
            summary = get_visible_pallet_summary(
                db,
                user=user,
                pallet_id=visible_pallet.id,
                include_inactive=include_inactive,
            )
        except PalletRuleError as exc:
            raise BarcodeNotFoundError("barcode not found") from exc
        lot = db.get(Lot, pallet.lot_id)
        if lot is None:
            raise BarcodeNotFoundError("barcode not found")
        return {
            "entity_kind": kind.value,
            "entity_id": pallet.id,
            "barcode": identity.barcode,
            "lifecycle_state": lifecycle,
            "retired": False,
            "frontend_path": f"/pallets/{pallet.id}",
            "display_label": f"Pallet {pallet.pallet_number}",
            "hierarchy": {
                "lot": {"id": lot.id, "barcode": lot.barcode, "name": lot.name},
                "pallet": {
                    "id": pallet.id,
                    "barcode": pallet.barcode,
                    "number": pallet.pallet_number,
                },
                "visible_warehouse_ids": summary.warehouse_ids,
                "visible_warehouse_names": summary.warehouse_names,
            },
            "redirect": redirect,
            "retired_at": None,
            "retirement_reason": None,
            "retirement_operation": None,
            "retirement_metadata": None,
        }

    if kind == BarcodeEntityKind.box:
        box = db.get(Box, identity.entity_id)
        if box is None or not can_access(user, box.current_warehouse_id):
            raise BarcodeNotFoundError("barcode not found")
        lifecycle = (
            "archived"
            if box.archived_at is not None
            else "returned"
            if box.status == BoxStatus.returned
            else "active"
        )
        hierarchy: dict[str, object] = {
            "lot": {
                "id": box.lot_record.id,
                "barcode": box.lot_record.barcode,
                "name": box.lot_record.name,
            },
            "box": {
                "id": box.id,
                "barcode": box.barcode,
                "number": box.box_number,
            },
            "warehouse_id": box.current_warehouse_id,
        }
        if box.pallet is not None:
            hierarchy["pallet"] = {
                "id": box.pallet.id,
                "barcode": box.pallet.barcode,
                "number": box.pallet.pallet_number,
            }
        return {
            "entity_kind": kind.value,
            "entity_id": box.id,
            "barcode": identity.barcode,
            "lifecycle_state": lifecycle,
            "retired": False,
            "frontend_path": f"/boxes/{box.id}",
            "display_label": f"Box {box.box_number}",
            "hierarchy": hierarchy,
            "redirect": None,
            "retired_at": None,
            "retirement_reason": None,
            "retirement_operation": None,
            "retirement_metadata": None,
        }

    file = db.get(BoxFile, identity.entity_id)
    if file is None or not can_access(user, file.box.current_warehouse_id):
        raise BarcodeNotFoundError("barcode not found")
    hierarchy = {
        "lot": {
            "id": file.lot.id,
            "barcode": file.lot.barcode,
            "name": file.lot.name,
        },
        "box": {
            "id": file.box.id,
            "barcode": file.box.barcode,
            "number": file.box.box_number,
        },
        "warehouse_id": file.box.current_warehouse_id,
    }
    if file.box.pallet is not None:
        hierarchy["pallet"] = {
            "id": file.box.pallet.id,
            "barcode": file.box.pallet.barcode,
            "number": file.box.pallet.pallet_number,
        }
    return {
        "entity_kind": kind.value,
        "entity_id": file.id,
        "barcode": identity.barcode,
        "lifecycle_state": (
            "archived" if file.archived_at is not None else "active"
        ),
        "retired": False,
        "frontend_path": f"/files/{file.id}",
        "display_label": f"File {file.reference}",
        "hierarchy": hierarchy,
        "redirect": None,
        "retired_at": None,
        "retirement_reason": None,
        "retirement_operation": None,
        "retirement_metadata": None,
    }


__all__ = [
    "BARCODE_PATTERN",
    "BarcodeRetirementTarget",
    "BarcodeLookupError",
    "BarcodeNotFoundError",
    "InvalidBarcodeError",
    "barcode_retirement_target",
    "barcode_identity_lock_statement",
    "MAX_ISSUANCE_NUMBER",
    "PREFIX_BY_KIND",
    "format_barcode",
    "finalize_barcode_identity",
    "is_valid_barcode",
    "issue_barcode_identity",
    "issue_pending_barcode_identities",
    "mod10_check_digit",
    "parse_barcode",
    "preserve_pending_barcode_snapshots",
    "reserve_issuance_number",
    "resolve_barcode",
    "retire_barcode_identity",
    "retire_barcode_identities",
]
