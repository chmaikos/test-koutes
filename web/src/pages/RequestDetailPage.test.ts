import { describe, expect, it } from "vitest";
import type {
  BoxRequest,
  RequestDirection,
  RequestOrigin,
  RequestStatus,
  Role,
  User,
} from "@/api/types";
import { requestPermissions } from "@/pages/requestPermissions";

function user(id: number, role: Role): User {
  return {
    id,
    role,
    entra_oid: `oid-${id}`,
    email: `user${id}@example.com`,
    display_name: `User ${id}`,
    role_override: false,
    is_active: true,
    email_alerts_enabled: true,
    last_login_at: null,
    username: null,
    is_local: false,
    must_change_credentials: false,
    warehouse_ids: [1],
  };
}

function request(
  direction: RequestDirection,
  status: RequestStatus,
  origin: RequestOrigin = "workflow",
): BoxRequest {
  return {
    id: 10,
    direction,
    status,
    warehouse_id: 1,
    quantity: 2,
    requester_user_id: 1,
    requester_name: "Requester",
    source_inbound_request_id: null,
    origin,
    suggestion_quantity: 2,
    current_available: 0,
    min_inventory: 2,
    pending_inbound: 0,
    eligible_return: 2,
    actual_received_quantity: null,
    variance_quantity: null,
    rejection_reason: null,
    cancellation_reason: null,
    discrepancy_reason: null,
    submitted_at: "2026-07-31T08:00:00Z",
    approved_at: null,
    in_transit_at: null,
    completed_at: null,
    created_at: "2026-07-31T08:00:00Z",
    updated_at: "2026-07-31T08:00:00Z",
    version: 1,
    items: [],
    documents: [],
  };
}

describe("request workflow permissions", () => {
  const requester = user(1, "viewer");
  const mover = user(2, "warehouse_mover");
  const operator = user(3, "operator");

  it("routes an inbound order from mover approval to requester acceptance", () => {
    expect(requestPermissions(request("inbound", "submitted"), mover)).toMatchObject({
      canApprove: true,
      canUpload: false,
      canComplete: false,
    });
    expect(requestPermissions(request("inbound", "approved"), mover)).toMatchObject({
      canMove: true,
      canUpload: true,
      canComplete: false,
    });
    expect(
      requestPermissions(request("inbound", "in_transit"), requester),
    ).toMatchObject({
      canMove: false,
      canComplete: true,
      canCancel: false,
    });
    expect(
      requestPermissions(request("inbound", "in_transit"), mover).canComplete,
    ).toBe(false);
  });

  it("routes a return to warehouse-mover acceptance", () => {
    expect(
      requestPermissions(request("return", "in_transit"), mover).canComplete,
    ).toBe(true);
    expect(
      requestPermissions(request("return", "in_transit"), requester).canComplete,
    ).toBe(false);
  });

  it("does not grant fulfilment actions to ordinary operators", () => {
    const permissions = requestPermissions(
      request("inbound", "submitted"),
      operator,
    );
    expect(permissions.canApprove).toBe(false);
    expect(permissions.canUpload).toBe(false);
    expect(permissions.canMove).toBe(false);
  });

  it("allows receipt owners to attach ERP documents to completed imports", () => {
    expect(
      requestPermissions(
        request("inbound", "completed", "xlsx_import"),
        requester,
      ).canUpload,
    ).toBe(true);
    expect(
      requestPermissions(
        request("inbound", "completed", "manual_entry"),
        operator,
      ).canUpload,
    ).toBe(false);
    expect(
      requestPermissions(request("inbound", "completed"), requester).canUpload,
    ).toBe(false);
  });
});
