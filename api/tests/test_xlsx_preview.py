from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook

from app.deps import get_current_user
from app.main import app
from app.models.users import UserRole
from app.services.xlsx_preview import preview_xlsx


def _workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PR100"
    sheet.append(["PR100"])
    sheet.append(["Sequence", "Box", "Range", "Notes"])
    sheet.append([1, 1, "001 - 050", "First"])
    sheet.append([2, 2, "051 - 100", None])
    sheet.append([None, None, None, None])
    other = workbook.create_sheet("Alternative")
    other.append(["Number", "Lot"])
    other.append([3, "LOT-B"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _as_user(user) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def test_preview_preserves_sheet_rows_and_does_not_assume_headers():
    sheets = preview_xlsx(_workbook_bytes())
    assert [sheet.name for sheet in sheets] == ["PR100", "Alternative"]
    assert [row.row_number for row in sheets[0].rows] == [1, 2, 3, 4]
    assert sheets[0].rows[2].cells == ["1", "1", "001 - 050", "First"]
    assert sheets[1].rows[1].cells == ["3", "LOT-B"]


def test_requester_can_preview_and_other_users_cannot(
    client, make_user, monkeypatch
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    _as_user(requester)
    request_id = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": 2},
    ).json()["id"]

    _as_user(mover)
    version = client.get(f"/api/requests/{request_id}").json()["version"]
    client.post(
        f"/api/requests/{request_id}/approve",
        json={"expected_version": version},
    )
    monkeypatch.setattr("app.routers.requests.put_document", lambda **_: None)
    version = client.get(f"/api/requests/{request_id}").json()["version"]
    client.post(
        f"/api/requests/{request_id}/documents",
        data={
            "document_type": "delivery_note",
            "erp_reference": "DN-XLSX",
            "expected_version": str(version),
        },
        files={"file": ("note.pdf", b"%PDF- preview", "application/pdf")},
    )
    for action in ("prepare", "mark-ready", "start-transit", "mark-arrived"):
        version = client.get(f"/api/requests/{request_id}").json()["version"]
        response = client.post(
            f"/api/requests/{request_id}/{action}",
            json={"expected_version": version},
        )
        assert response.status_code == 200

    _as_user(requester)
    response = client.post(
        f"/api/requests/{request_id}/inbound-xlsx-preview",
        files={
            "file": (
                "variable-layout.xlsx",
                _workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200
    assert response.json()["sheets"][0]["rows"][0] == {
        "row_number": 1,
        "cells": ["PR100"],
    }

    outsider = make_user(UserRole.viewer)
    _as_user(outsider)
    denied = client.post(
        f"/api/requests/{request_id}/inbound-xlsx-preview",
        files={"file": ("boxes.xlsx", _workbook_bytes())},
    )
    assert denied.status_code == 403
