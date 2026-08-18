import { describe, expect, it } from "vitest";
import type {
  BoxRequest,
  RequestDirection,
  RequestOrigin,
  RequestStatus,
  Role,
  User,
} from "@/api/types";
import { requestStatusLabel } from "@/components/RequestStatusBadge";
import { requestPermissions } from "@/pages/requestPermissions";
import {
  canRetryRequestConflict,
  conflictDetail,
} from "@/pages/requestConflict";

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
    email_requests_enabled: true,
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
    target_warehouse_id: direction === "return" ? 1 : null,
    quantity: 2,
    requester_user_id: 1,
    requester_name: "Requester",
    priority: "normal",
    requested_date: null,
    scheduled_window_start: null,
    scheduled_window_end: null,
    sla_deadline: null,
    assigned_mover_user_id: null,
    assigned_mover_name: null,
    destination_contact: null,
    internal_location: null,
    special_handling_instructions: null,
    source_inbound_request_id: null,
    parent_request_id: null,
    root_request_id: 10,
    child_request_ids: [],
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
    receipt_restore_archived: false,
    receipt_quarantine: false,
    receipt_document_required: false,
    submitted_at: "2026-07-31T08:00:00Z",
    approved_at: null,
    approved_by_user_id: null,
    preparing_at: null,
    preparing_by_user_id: null,
    ready_for_transport_at: null,
    ready_for_transport_by_user_id: null,
    in_transit_at: null,
    in_transit_by_user_id: null,
    awaiting_confirmation_at: null,
    awaiting_confirmation_by_user_id: null,
    completed_at: null,
    completed_by_user_id: null,
    created_at: "2026-07-31T08:00:00Z",
    updated_at: "2026-07-31T08:00:00Z",
    version: 1,
    items: [],
    documents: [],
    discrepancies: [],
    comments: [],
    attachments: [],
    operational_exceptions: [],
    current_exception: null,
    permissions: {
      can_assign: false,
      can_schedule: false,
      can_comment: true,
      can_attach: true,
      can_prepare: false,
      can_mark_ready: false,
      can_start_transit: false,
      can_mark_arrived: false,
      can_confirm: false,
      can_hold: false,
      can_resume: false,
      can_reschedule: false,
      can_report_failed: false,
      can_retry: false,
    },
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
      canPrepare: true,
      canComplete: false,
    });
    expect(
      requestPermissions(request("inbound", "in_transit"), requester),
    ).toMatchObject({
      canMove: false,
      canComplete: false,
      canCancel: false,
    });
    expect(
      requestPermissions(request("inbound", "in_transit"), mover).canMarkArrived,
    ).toBe(true);
    expect(
      requestPermissions(
        request("inbound", "awaiting_confirmation"),
        requester,
      ).canComplete,
    ).toBe(true);
  });

  it("routes a return to warehouse-mover acceptance", () => {
    expect(
      requestPermissions(
        request("return", "awaiting_confirmation"),
        mover,
      ).canComplete,
    ).toBe(true);
    expect(
      requestPermissions(
        request("return", "awaiting_confirmation"),
        requester,
      ).canComplete,
    ).toBe(false);
  });

  it("blocks lifecycle actions while an exception awaits recovery", () => {
    const held = request("inbound", "preparing");
    held.current_exception = {
      id: 1,
      exception_kind: "hold",
      reason: "Carrier unavailable",
      revised_window_start: null,
      revised_window_end: null,
      resume_target: "preparing",
      created_by_user_id: mover.id,
      created_at: "2026-08-10T08:00:00Z",
      resolved_by_user_id: null,
      resolved_at: null,
      resolution: null,
    };
    expect(requestPermissions(held, mover)).toMatchObject({
      canMarkReady: false,
      canHold: false,
      canResume: true,
      canReschedule: false,
    });

    held.current_exception = {
      ...held.current_exception,
      exception_kind: "failed_delivery",
      resume_target: "ready_for_transport",
    };
    expect(requestPermissions(held, mover)).toMatchObject({
      canResume: false,
      canRetry: true,
    });
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

describe("request workflow labels", () => {
  it("uses direction-specific labels for every transport handoff", () => {
    expect(requestStatusLabel("preparing", "inbound")).toBe("Preparing inbound");
    expect(requestStatusLabel("preparing", "return")).toBe("Preparing return");
    expect(requestStatusLabel("ready_for_transport", "inbound")).toBe(
      "Ready for dispatch",
    );
    expect(requestStatusLabel("ready_for_transport", "return")).toBe(
      "Ready for collection",
    );
    expect(requestStatusLabel("in_transit", "inbound")).toBe("Delivering");
    expect(requestStatusLabel("in_transit", "return")).toBe("Collecting");
    expect(requestStatusLabel("awaiting_confirmation", "inbound")).toBe(
      "Delivered · awaiting requester confirmation",
    );
    expect(requestStatusLabel("awaiting_confirmation", "return")).toBe(
      "Collected · awaiting warehouse confirmation",
    );
  });

  it("keeps generic labels when direction is unavailable", () => {
    expect(requestStatusLabel("ready_for_transport")).toBe("Ready for transport");
    expect(requestStatusLabel("completed", "return")).toBe("Completed");
  });
});

describe("request version conflict recovery", () => {
  const detail = {
    message: "request changed; refresh and retry",
    request_id: 10,
    latest_version: 4,
    latest_status: "in_transit" as const,
    relevant_events: [],
  };

  it("extracts structured API conflict details", () => {
    expect(
      conflictDetail({
        response: { status: 409, data: { detail } },
      }),
    ).toEqual(detail);
    expect(conflictDetail({ response: { status: 400 } })).toBeNull();
  });

  it("only retries after refetch when status and version still match", () => {
    expect(
      canRetryRequestConflict({ status: "in_transit", version: 4 }, detail),
    ).toBe(true);
    expect(
      canRetryRequestConflict({ status: "completed", version: 5 }, detail),
    ).toBe(false);
  });
});
