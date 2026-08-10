from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: int | None
    warehouse_id: int
    kind: str
    title: str
    body: str
    deep_link: str
    read_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    total: int
    unread: int
    page: int
    page_size: int


class MarkNotificationsRead(BaseModel):
    notification_ids: list[int] | None = Field(
        default=None, max_length=500
    )


class UnreadCountOut(BaseModel):
    unread: int
