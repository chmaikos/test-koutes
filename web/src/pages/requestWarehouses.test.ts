import { describe, expect, it } from "vitest";
import {
  activeTargetWarehouses,
  defaultTargetWarehouseId,
  requestWarehousePayload,
  requestWarehouseRoute,
  requestWarehouseValidationError,
} from "@/pages/requestWarehouses";

const warehouses = [
  { id: 1, name: "Source", is_active: true },
  { id: 2, name: "Target", is_active: true },
  { id: 3, name: "Old depot", is_active: false },
];

describe("request warehouse routes", () => {
  it("renders return routes from source to target", () => {
    expect(
      requestWarehouseRoute(
        {
          direction: "return",
          warehouse_id: 1,
          target_warehouse_id: 2,
        },
        warehouses,
      ),
    ).toMatchObject({
      sourceName: "Source",
      targetName: "Target",
      targetWarehouseId: 2,
      label: "Source → Target",
    });
  });

  it("falls back legacy null return targets to the source", () => {
    expect(
      requestWarehouseRoute(
        {
          direction: "return",
          warehouse_id: 3,
          target_warehouse_id: null,
        },
        warehouses,
      ).label,
    ).toBe("Old depot (archived) → Old depot (archived)");
  });

  it("keeps inbound warehouse labels unchanged", () => {
    expect(
      requestWarehouseRoute(
        {
          direction: "inbound",
          warehouse_id: 1,
          target_warehouse_id: null,
        },
        warehouses,
      ).label,
    ).toBe("Source");
  });
});

describe("create request warehouse selection", () => {
  it("defaults and resets the target with direction and source changes", () => {
    expect(defaultTargetWarehouseId("inbound", 1)).toBeUndefined();
    expect(defaultTargetWarehouseId("return", 1)).toBe(1);
    expect(defaultTargetWarehouseId("return", 2)).toBe(2);
    expect(defaultTargetWarehouseId("return", undefined)).toBeUndefined();
    expect(defaultTargetWarehouseId("inbound", 2)).toBeUndefined();
  });

  it("only offers active warehouses as targets", () => {
    expect(activeTargetWarehouses(warehouses).map(({ id }) => id)).toEqual([
      1, 2,
    ]);
  });

  it("requires an explicit active target for returns", () => {
    expect(
      requestWarehouseValidationError("return", undefined, warehouses),
    ).toBe("Choose a target warehouse.");
    expect(requestWarehouseValidationError("return", 3, warehouses)).toBe(
      "Choose an active target warehouse.",
    );
    expect(requestWarehouseValidationError("return", 99, warehouses)).toBe(
      "Choose an active target warehouse.",
    );
    expect(requestWarehouseValidationError("return", 2, warehouses)).toBeNull();
  });

  it("builds direction-specific source and target payloads", () => {
    expect(
      requestWarehousePayload("return", 1, 2, warehouses),
    ).toStrictEqual({
      warehouse_id: 1,
      target_warehouse_id: 2,
    });
    expect(
      requestWarehousePayload("inbound", 1, undefined, warehouses),
    ).toStrictEqual({ warehouse_id: 1 });
    expect(() =>
      requestWarehousePayload("return", 1, undefined, warehouses),
    ).toThrow("Choose a target warehouse.");
  });
});
