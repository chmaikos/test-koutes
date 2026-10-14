import pytest
from sqlalchemy import func, select

from app.deps import get_current_user
from app.main import app
from app.models.requests import (
    BoxRequest,
    BoxRequestDiscrepancy,
    BoxRequestDiscrepancyPhoto,
    BoxRequestDiscrepancyType,
)
from app.models.users import UserRole


@pytest.mark.parametrize(
    "actor_role,is_requester,expected",
    [
        (UserRole.viewer, False, 403),
        (UserRole.operator, False, 403),
        (UserRole.viewer, True, 201),
        (UserRole.warehouse_mover, False, 201),
    ],
)
def test_photo_upload_matches_attachment_permissions(
    client, session, make_user, monkeypatch, actor_role, is_requester, expected
):
    requester = make_user(UserRole.viewer)
    app.dependency_overrides[get_current_user] = lambda: requester
    created = client.post(
        "/api/requests", json={"direction": "inbound", "warehouse_id": 1, "quantity": 1}
    )
    assert created.status_code == 201
    request_id = created.json()["id"]
    discrepancy = BoxRequestDiscrepancy(
        request_id=request_id, discrepancy_type=BoxRequestDiscrepancyType.missing, quantity=1
    )
    session.add(discrepancy)
    session.commit()
    actor = requester if is_requester else make_user(actor_role)
    app.dependency_overrides[get_current_user] = lambda: actor
    writes = []
    monkeypatch.setattr("app.routers.requests.put_document", lambda **kwargs: writes.append(kwargs))
    response = client.post(
        f"/api/requests/{request_id}/discrepancies/{discrepancy.id}/photos",
        data={"expected_version": str(created.json()["version"])},
        files={"file": ("photo.jpg", b"\xff\xd8\xffphoto", "image/jpeg")},
    )
    assert response.status_code == expected
    session.expire_all()
    request = session.get(BoxRequest, request_id)
    count = session.scalar(select(func.count(BoxRequestDiscrepancyPhoto.id)))
    if expected == 403:
        assert not writes
        assert count == 0
        assert request.version == created.json()["version"]
    else:
        assert len(writes) == 1
        assert count == 1
        assert request.version == created.json()["version"] + 1
