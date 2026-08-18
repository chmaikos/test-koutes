import type {
  BoxRequest,
  RequestDirection,
  Warehouse,
} from "@/api/types";

type WarehouseReference = Pick<Warehouse, "id" | "name" | "is_active">;
type RequestWarehouseReference = Pick<
  BoxRequest,
  "direction" | "warehouse_id" | "target_warehouse_id"
>;

export function activeTargetWarehouses(
  warehouses: readonly WarehouseReference[],
): WarehouseReference[] {
  return warehouses.filter((warehouse) => warehouse.is_active);
}

export function defaultTargetWarehouseId(
  direction: RequestDirection,
  sourceWarehouseId: number | undefined,
): number | undefined {
  return direction === "return" ? sourceWarehouseId : undefined;
}

export function requestWarehouseValidationError(
  direction: RequestDirection,
  targetWarehouseId: number | undefined,
  warehouses: readonly WarehouseReference[],
): string | null {
  if (direction === "inbound") return null;
  if (targetWarehouseId === undefined) return "Choose a target warehouse.";
  if (
    !warehouses.some(
      (warehouse) =>
        warehouse.id === targetWarehouseId && warehouse.is_active,
    )
  ) {
    return "Choose an active target warehouse.";
  }
  return null;
}

export function requestWarehousePayload(
  direction: "inbound",
  sourceWarehouseId: number,
  targetWarehouseId: number | undefined,
  warehouses: readonly WarehouseReference[],
): { warehouse_id: number };
export function requestWarehousePayload(
  direction: "return",
  sourceWarehouseId: number,
  targetWarehouseId: number | undefined,
  warehouses: readonly WarehouseReference[],
): { warehouse_id: number; target_warehouse_id: number };
export function requestWarehousePayload(
  direction: RequestDirection,
  sourceWarehouseId: number,
  targetWarehouseId: number | undefined,
  warehouses: readonly WarehouseReference[],
):
  | { warehouse_id: number }
  | { warehouse_id: number; target_warehouse_id: number } {
  const error = requestWarehouseValidationError(
    direction,
    targetWarehouseId,
    warehouses,
  );
  if (error) throw new Error(error);
  return direction === "return"
    ? {
        warehouse_id: sourceWarehouseId,
        target_warehouse_id: targetWarehouseId!,
      }
    : { warehouse_id: sourceWarehouseId };
}

export function warehouseDisplayName(
  warehouseId: number,
  warehouses: readonly WarehouseReference[],
): string {
  const warehouse = warehouses.find(
    (candidate) => candidate.id === warehouseId,
  );
  if (!warehouse) return `#${warehouseId}`;
  return `${warehouse.name}${warehouse.is_active ? "" : " (archived)"}`;
}

export function requestWarehouseRoute(
  request: RequestWarehouseReference,
  warehouses: readonly WarehouseReference[],
): {
  sourceName: string;
  targetName: string | null;
  targetWarehouseId: number | null;
  label: string;
} {
  const sourceName = warehouseDisplayName(request.warehouse_id, warehouses);
  if (request.direction === "inbound") {
    return {
      sourceName,
      targetName: null,
      targetWarehouseId: null,
      label: sourceName,
    };
  }

  const targetWarehouseId =
    request.target_warehouse_id ?? request.warehouse_id;
  const targetName = warehouseDisplayName(targetWarehouseId, warehouses);
  return {
    sourceName,
    targetName,
    targetWarehouseId,
    label: `${sourceName} → ${targetName}`,
  };
}
