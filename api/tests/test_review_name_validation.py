import pytest
from pydantic import ValidationError

from app.schemas.employees import EmployeeCreate, EmployeeImportItem, EmployeeUpdate
from app.schemas.warehouses import WarehouseCreate, WarehouseUpdate


@pytest.mark.parametrize(
    "schema,field,extra",
    [
        (EmployeeCreate, "full_name", {"warehouse_id": 1}),
        (EmployeeUpdate, "full_name", {}),
        (EmployeeImportItem, "full_name", {"source_row": 1}),
        (WarehouseCreate, "name", {}),
        (WarehouseUpdate, "name", {}),
    ],
)
def test_names_are_normalized_before_length_validation(schema, field, extra):
    with pytest.raises(ValidationError):
        schema(**{field: " \t\n", **extra})
    assert getattr(schema(**{field: "  Alice  ", **extra}), field) == "Alice"
