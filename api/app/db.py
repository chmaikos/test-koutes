from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

engine_options: dict[str, object] = {
    "pool_pre_ping": True,
    "future": True,
}
if make_url(settings.database_url).get_backend_name() != "sqlite":
    engine_options.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
    )

engine = create_engine(
    settings.database_url,
    **engine_options,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


@event.listens_for(SessionLocal, "before_flush")
def _finalize_pending_barcode_identities(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    """Enforce the live-entity identity invariant for indirect producers."""
    from app.services.barcodes import (
        issue_pending_barcode_identities,
        preserve_pending_barcode_snapshots,
    )

    issue_pending_barcode_identities(
        session,
        reason="Production safety-net issuance for an indirect creation path.",
    )
    preserve_pending_barcode_snapshots(session)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
