from __future__ import annotations

from app.deps import get_current_user
from app.main import app
from app.models.users import UserRole
from app.models.warehouses import Warehouse
from app.models.xlsx_mapping_templates import XlsxMappingTemplate, XlsxMappingUseCase


def _as_user(user) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _payload(
    name: str,
    *,
    use_case: str = "box_import",
    warehouse_id: int | None = None,
    filename: str = "incoming-boxes.xlsx",
    headers: list[str] | None = None,
) -> dict:
    return {
        "use_case": use_case,
        "name": name,
        "warehouse_id": warehouse_id,
        "sheet_pattern": "Boxes*",
        "filename": filename,
        "headers": headers or ["Box Number", "Lot", "Contents", "Pallet"],
        "column_mappings": {
            "box_number": {"index": 0, "header": "Box Number"},
            "lot": {"index": 1, "header": "Lot"},
            "file_reference": {"index": 2, "header": "Contents"},
            "contents": {"index": 2, "header": "Contents"},
            "pallet_number": {"index": 3, "header": "Pallet"},
        },
        "lot_source": "column",
        "row_start": 2,
        "include_rows_by_default": True,
    }


def test_legacy_template_is_returned_as_incomplete(client, make_user, session):
    owner = make_user(UserRole.operator)
    session.add(
        XlsxMappingTemplate(
            owner_user_id=owner.id,
            use_case=XlsxMappingUseCase.box_import,
            name="Legacy contents template",
            sheet_pattern="*",
            filename_fingerprint="legacy",
            header_fingerprint="box\x1flot\x1fdescription",
            column_mappings={
                "box_number": {"index": 0, "header": "Box"},
                "lot": {"index": 1, "header": "Lot"},
                "contents": {"index": 2, "header": "Description"},
                "barcode": {"index": 3, "header": "Legacy file barcode"},
            },
            lot_source="column",
            row_start=2,
        )
    )
    session.commit()
    _as_user(owner)

    response = client.get(
        "/api/xlsx-mapping-templates", params={"use_case": "box_import"}
    )
    assert response.status_code == 200
    assert response.json()[0]["is_legacy_incomplete"] is True
    assert response.json()[0]["column_mappings"]["file_reference"] is None
    assert "barcode" not in response.json()[0]["column_mappings"]


def test_template_acl_and_owner_only_mutations(client, make_user, session):
    owner = make_user(UserRole.operator)
    colleague = make_user(UserRole.operator)
    outsider = make_user(UserRole.operator)
    outsider.warehouses = []
    session.commit()

    _as_user(owner)
    private = client.post(
        "/api/xlsx-mapping-templates", json=_payload("Private")
    )
    assert private.status_code == 201
    private_id = private.json()["id"]
    shared = client.post(
        "/api/xlsx-mapping-templates",
        json=_payload("Shared", warehouse_id=1),
    )
    assert shared.status_code == 201
    shared_id = shared.json()["id"]

    _as_user(colleague)
    listed = client.get(
        "/api/xlsx-mapping-templates",
        params={"use_case": "box_import", "warehouse_id": 1},
    )
    assert [item["name"] for item in listed.json()] == ["Shared"]
    assert listed.json()[0]["is_owner"] is False
    assert (
        client.patch(
            f"/api/xlsx-mapping-templates/{private_id}",
            json={"name": "Exposed private"},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/xlsx-mapping-templates/{shared_id}",
            json={"name": "Stolen"},
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/api/xlsx-mapping-templates/{shared_id}").status_code
        == 403
    )

    _as_user(outsider)
    assert (
        client.get(
            "/api/xlsx-mapping-templates",
            params={"use_case": "box_import", "warehouse_id": 1},
        ).json()
        == []
    )
    assert (
        client.post(f"/api/xlsx-mapping-templates/{shared_id}/use").status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/xlsx-mapping-templates/{shared_id}",
            json={"name": "Cross-warehouse"},
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/xlsx-mapping-templates/{shared_id}").status_code
        == 404
    )

    _as_user(owner)
    updated = client.patch(
        f"/api/xlsx-mapping-templates/{shared_id}",
        json={"name": "Updated", "warehouse_id": None},
    )
    assert updated.status_code == 200
    assert updated.json()["is_shared"] is False
    assert client.delete(f"/api/xlsx-mapping-templates/{shared_id}").status_code == 204


def test_sharing_requires_warehouse_access(client, make_user, session):
    owner = make_user(UserRole.operator)
    owner.warehouses = []
    session.commit()
    _as_user(owner)
    response = client.post(
        "/api/xlsx-mapping-templates",
        json=_payload("No access", warehouse_id=1),
    )
    assert response.status_code == 403


def test_suggestions_rank_exact_normalized_headers_first(client, make_user):
    owner = make_user(UserRole.operator)
    _as_user(owner)
    exact = client.post(
        "/api/xlsx-mapping-templates",
        json=_payload(
            "Header winner",
            filename="unrelated.xlsx",
            headers=["BOX number", " Lot ", "CONTENTS"],
        ),
    ).json()
    client.post(
        "/api/xlsx-mapping-templates",
        json=_payload(
            "Filename winner",
            filename="daily-delivery.xlsx",
            headers=["Reference", "Batch"],
        ),
    )

    response = client.post(
        "/api/xlsx-mapping-templates/suggestions",
        json={
            "use_case": "box_import",
            "filename": "daily-delivery.xlsx",
            "sheet_name": "Boxes August",
            "header_candidates": [
                ["Report title"],
                [" box NUMBER ", "LOT", "contents"],
            ],
        },
    )
    assert response.status_code == 200
    suggestions = response.json()
    assert suggestions[0]["template"]["id"] == exact["id"]
    assert suggestions[0]["exact_header_match"] is True
    assert suggestions[0]["confidence"] >= 0.98
    assert "Exact normalized header" in suggestions[0]["explanation"]
    assert (
        client.post(
            f"/api/xlsx-mapping-templates/{exact['id']}/use"
        ).json()["usage_count"]
        == 1
    )


def test_template_config_validation_and_use_case_filtering(client, make_user):
    owner = make_user(UserRole.operator)
    _as_user(owner)
    invalid = _payload("Invalid")
    invalid["column_mappings"].pop("lot")
    response = client.post("/api/xlsx-mapping-templates", json=invalid)
    assert response.status_code == 422
    retired_barcode = _payload("Retired barcode")
    retired_barcode["column_mappings"]["barcode"] = {
        "index": 4,
        "header": "Barcode",
    }
    response = client.post(
        "/api/xlsx-mapping-templates", json=retired_barcode
    )
    assert response.status_code == 422
    missing_pallet = _payload("Missing pallet")
    missing_pallet["column_mappings"].pop("pallet_number")
    response = client.post("/api/xlsx-mapping-templates", json=missing_pallet)
    assert response.status_code == 201, response.text
    assert response.json()["column_mappings"]["pallet_number"] is None

    inbound = client.post(
        "/api/xlsx-mapping-templates",
        json=_payload("Inbound", use_case="inbound_acceptance"),
    )
    assert inbound.status_code == 201
    assert inbound.json()["column_mappings"]["pallet_number"] == {
        "index": 3,
        "header": "Pallet",
    }
    listed_box_imports = client.get(
        "/api/xlsx-mapping-templates",
        params={"use_case": "box_import"},
    ).json()
    assert [template["name"] for template in listed_box_imports] == [
        "Missing pallet"
    ]


def test_admin_sees_all_shared_but_not_other_private_or_owner_controls(
    client, make_user
):
    first_owner = make_user(UserRole.operator)
    second_owner = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)

    _as_user(first_owner)
    client.post("/api/xlsx-mapping-templates", json=_payload("First private"))
    first_shared = client.post(
        "/api/xlsx-mapping-templates",
        json=_payload("First shared", warehouse_id=1),
    ).json()

    _as_user(second_owner)
    client.post("/api/xlsx-mapping-templates", json=_payload("Second private"))
    client.post(
        "/api/xlsx-mapping-templates",
        json=_payload("Second shared", warehouse_id=2),
    )

    _as_user(admin)
    listed = client.get(
        "/api/xlsx-mapping-templates", params={"use_case": "box_import"}
    )
    assert {item["name"] for item in listed.json()} == {
        "First shared",
        "Second shared",
    }
    assert (
        client.patch(
            f"/api/xlsx-mapping-templates/{first_shared['id']}",
            json={"name": "Admin takeover"},
        ).status_code
        == 403
    )


def test_shared_visibility_does_not_cross_warehouse_grants(
    client, make_user, session
):
    owner = make_user(UserRole.operator)
    limited = make_user(UserRole.operator)
    limited.warehouses = [session.get(Warehouse, 1)]
    session.commit()

    _as_user(owner)
    client.post(
        "/api/xlsx-mapping-templates",
        json=_payload("Building 1", warehouse_id=1),
    )
    second = client.post(
        "/api/xlsx-mapping-templates",
        json=_payload("Building 2", warehouse_id=2),
    ).json()

    _as_user(limited)
    listed = client.get(
        "/api/xlsx-mapping-templates", params={"use_case": "box_import"}
    )
    assert [item["name"] for item in listed.json()] == ["Building 1"]
    assert (
        client.post(
            f"/api/xlsx-mapping-templates/{second['id']}/use"
        ).status_code
        == 404
    )


def test_fixed_lot_and_unicode_header_fingerprint(client, make_user):
    owner = make_user(UserRole.operator)
    _as_user(owner)
    payload = _payload(
        "Greek fixed",
        use_case="inbound_acceptance",
        headers=["Αριθμός Κιβωτίου", "Περιεχόμενα"],
    )
    payload["column_mappings"].pop("lot")
    payload["lot_source"] = "fixed"
    payload["fixed_lot"] = "PR300"
    created = client.post("/api/xlsx-mapping-templates", json=payload)
    assert created.status_code == 201
    assert created.json()["fixed_lot"] == "PR300"

    suggested = client.post(
        "/api/xlsx-mapping-templates/suggestions",
        json={
            "use_case": "inbound_acceptance",
            "filename": "other.xlsx",
            "sheet_name": "Boxes August",
            "header_candidates": [["ΑΡΙΘΜΟΣ ΚΙΒΩΤΙΟΥ", "ΠΕΡΙΕΧΟΜΕΝΑ"]],
        },
    )
    assert suggested.status_code == 200
    assert suggested.json()[0]["exact_header_match"] is True


def test_template_names_are_unique_per_owner_and_use_case(client, make_user):
    owner = make_user(UserRole.operator)
    other = make_user(UserRole.operator)

    _as_user(owner)
    assert (
        client.post(
            "/api/xlsx-mapping-templates", json=_payload("Daily")
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/xlsx-mapping-templates", json=_payload("Daily")
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/xlsx-mapping-templates",
            json=_payload("Daily", use_case="inbound_acceptance"),
        ).status_code
        == 201
    )

    _as_user(other)
    assert (
        client.post(
            "/api/xlsx-mapping-templates", json=_payload("Daily")
        ).status_code
        == 201
    )
