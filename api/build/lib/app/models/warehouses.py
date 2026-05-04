from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    min_inventory: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_capacity: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
