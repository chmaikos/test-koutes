import { describe, expect, it } from "vitest";
import type {
  LotForcePurgePreview,
  LotOption,
  LotPurgeBlocker,
  LotPurgePreview,
  LotStatusCounts,
  Role,
} from "@/api/types";
import {
  canPurgeLot,
  canReassignLot,
  completionLabel,
  exactLotMatch,
  forcePurgeChange,
  forcePurgeConfirmationIsValid,
  forcePurgeConflictFormState,
  forcePurgeHasHardBlock,
  forcePurgeSiblingPreservationText,
  lotConflictCurrent,
  lotForcePurgeConflict,
  lotForcePurgePayload,
  lotMergeCandidate,
  lotMergeConflictCode,
  lotMergePayload,
  lotPurgeBlockerText,
  lotPurgeConflict,
  lotPurgeEntityPath,
  lotPurgePayload,
  lotPurgeRemovalItems,
  lotRenamePayload,
  lotSelection,
  lotStatusSegments,
  normalizeLotName,
  parseLotSearchParams,
  purgeCleanupWarning,
  purgeConfirmationIsValid,
  purgeConflictFormState,
  purgeSuccessAction,
  reassignmentPayload,
  renameValidation,
  shouldOfferForcePurgeEscalation,
  shouldOfferLotCreation,
} from "@/pages/lots";

const counts: LotStatusCounts = {
  quarantined: 1,
  received: 2,
  processing: 0,
  incomplete: 3,
  ready_to_return: 0,
  returned: 4,
};

describe("lot completion and status display", () => {
  it("distinguishes a zero denominator from zero percent", () => {
    expect(completionLabel(null)).toBe("No eligible boxes");
    expect(completionLabel(0)).toBe("0.0%");
    expect(completionLabel(62.345)).toBe("62.3%");
  });

  it("builds accessible non-zero status segments", () => {
    expect(lotStatusSegments(counts)).toEqual([
      { status: "quarantined", count: 1, percent: 10 },
      { status: "received", count: 2, percent: 20 },
      { status: "incomplete", count: 3, percent: 30 },
      { status: "returned", count: 4, percent: 40 },
    ]);
  });
});

describe("lot URL filters", () => {
  it("parses valid filters, sorts, and pagination", () => {
    const result = parseLotSearchParams(
      new URLSearchParams(
        "q=Alpha&warehouse_id=3&progress=in_progress&sort_by=completion&sort_dir=asc&page=2&page_size=50",
      ),
    );
    expect(result).toEqual({
      filters: {
        search: "Alpha",
        warehouse_id: 3,
        progress_state: "in_progress",
        sort_by: "completion",
        sort_dir: "asc",
      },
      page: 2,
      pageSize: 50,
    });
  });

  it("drops invalid URL values", () => {
    expect(
      parseLotSearchParams(
        new URLSearchParams(
          "warehouse_id=-2&progress=done&sort_by=owner&page=-4&page_size=999",
        ),
      ),
    ).toEqual({ filters: {}, page: 1, pageSize: 25 });
  });
});

describe("lot picker identity", () => {
  const options: LotOption[] = [
    {
      id: 7,
      name: "Spring Intake",
      normalized_name: "spring intake",
      exact_normalized_match: true,
    },
  ];

  it("normalizes case and whitespace before duplicate checks", () => {
    expect(normalizeLotName("  SPRING\t intake ")).toBe("spring intake");
    expect(exactLotMatch(options, " SPRING   INTAKE ")?.id).toBe(7);
    expect(exactLotMatch(options, "New lot")).toBeUndefined();
    expect(lotSelection(options[0])).toEqual({
      id: 7,
      name: "Spring Intake",
    });
    expect(shouldOfferLotCreation(options, "New lot", true, 3)).toBe(true);
    expect(
      shouldOfferLotCreation(options, " SPRING INTAKE ", true, 3),
    ).toBe(false);
    expect(shouldOfferLotCreation(options, "New lot", false, 3)).toBe(false);
  });
});

describe("audited lot changes", () => {
  it("validates and builds rename payloads", () => {
    expect(renameValidation("Name", " ")).toBe(
      "A correction reason is required.",
    );
    expect(lotRenamePayload("  Corrected   Name ", " typo ", 4)).toEqual({
      new_name: "Corrected Name",
      reason: "typo",
      expected_version: 4,
    });
  });

  it("recovers latest state from a conflict response", () => {
    const current = {
      id: 1,
      name: "Latest",
      normalized_name: "latest",
      version: 3,
      updated_at: "2026-08-11T10:00:00Z",
    };
    expect(
      lotConflictCurrent({
        response: {
          status: 409,
          data: { detail: { code: "version_conflict", current } },
        },
      }),
    ).toEqual(current);
  });

  it("parses an allowed collision and builds an explicit merge payload", () => {
    const candidate = {
      source: { id: 1, name: "Source", version: 3 },
      target: { id: 2, name: "Target", version: 5 },
      merge_allowed: true,
      overlapping_box_numbers: [],
      overlapping_box_count: 0,
      overlap_list_truncated: false,
    };
    const error = {
      response: {
        status: 409,
        data: {
          detail: {
            code: "name_collision",
            merge_candidate: candidate,
          },
        },
      },
    };
    expect(lotMergeCandidate(error)).toEqual(candidate);
    expect(lotConflictCurrent(error)).toBeNull();
    expect(lotMergePayload(candidate, " duplicate identity ")).toEqual({
      target_lot_id: 2,
      reason: "duplicate identity",
      expected_source_version: 3,
      expected_target_version: 5,
    });
  });

  it("parses overlap-disabled and stale merge conflicts", () => {
    const blocked = {
      source: { id: 1, name: "Source", version: 4 },
      target: { id: 2, name: "Target", version: 6 },
      merge_allowed: false,
      overlapping_box_numbers: ["001", "009"],
      overlapping_box_count: 2,
      overlap_list_truncated: false,
    };
    const error = {
      response: {
        status: 409,
        data: {
          detail: {
            code: "target_version_conflict",
            merge_candidate: blocked,
          },
        },
      },
    };
    expect(lotMergeConflictCode(error)).toBe("target_version_conflict");
    expect(lotMergeCandidate(error)?.merge_allowed).toBe(false);
    expect(lotMergeCandidate(error)?.overlapping_box_numbers).toEqual([
      "001",
      "009",
    ]);
  });

  it("gates reassignment and builds the audited payload", () => {
    const roles: Role[] = ["admin", "warehouse_mover", "operator", "viewer"];
    expect(roles.filter(canReassignLot)).toEqual(["admin"]);
    expect(reassignmentPayload(9, " verified label ", 5)).toEqual({
      lot_id: 9,
      reason: "verified label",
      expected_lot_version: 5,
    });
  });
});

const purgePreview: LotPurgePreview = {
  lot_id: 12,
  lot_name: "Case Sensitive Lot",
  lot_version: 7,
  active_box_count: 0,
  active_box_ids: [],
  active_box_ids_truncated: false,
  archived_box_count: 2,
  archived_box_ids: [30, 31],
  archived_box_ids_truncated: false,
  linked_request_count: 1,
  linked_request_ids: [44],
  linked_request_ids_truncated: false,
  requests: [
    {
      request_id: 44,
      origin: "manual_entry",
      status: "completed",
      direction: "inbound",
      item_count: 2,
      lot_item_count: 2,
    },
  ],
  requests_truncated: false,
  object_key_count: 3,
  graph_signature: "a".repeat(64),
  eligible: true,
  blockers: [],
  confirmation_policy: "exact_case_sensitive_no_normalization",
};

describe("safe lot purge", () => {
  it("gates the active-lot danger zone to administrators", () => {
    const roles: Role[] = ["admin", "warehouse_mover", "operator", "viewer"];
    expect(roles.filter((role) => canPurgeLot(role, { id: 12 }))).toEqual([
      "admin",
    ]);
    expect(canPurgeLot("admin", { id: 12, state: "merged" })).toBe(false);
  });

  it("formats the preview removal scope and actionable blockers", () => {
    expect(lotPurgeRemovalItems(purgePreview)).toEqual([
      "Lot “Case Sensitive Lot” (version 7)",
      "2 archived boxes and their box history",
      "1 exclusive self-receipt and all owned request records",
      "3 stored objects",
    ]);
    const blocker: LotPurgeBlocker = {
      code: "mixed_lot_receipt",
      message: "A linked request contains items outside this lot.",
      remediation: "A linked request contains items outside this lot.",
      entities: [{ entity_type: "request", entity_id: 44 }],
      entity_count: 1,
      entities_truncated: false,
    };
    expect(lotPurgeBlockerText(blocker)).toContain("not exclusively owned");
    expect(lotPurgeEntityPath(blocker.entities[0], 12)).toBe("/requests/44");
    expect(
      lotPurgeEntityPath(
        { entity_type: "event", entity_id: 9 },
        purgePreview.lot_id,
      ),
    ).toBe("/lots/12#lot-audit-history");
  });

  it("requires exact confirmation and a mandatory bounded reason", () => {
    expect(
      purgeConfirmationIsValid(
        purgePreview,
        "Case Sensitive Lot",
        "duplicate receipt",
      ),
    ).toBe(true);
    expect(
      purgeConfirmationIsValid(
        purgePreview,
        "case sensitive lot",
        "duplicate receipt",
      ),
    ).toBe(false);
    expect(
      purgeConfirmationIsValid(
        purgePreview,
        "Case Sensitive Lot ",
        "duplicate receipt",
      ),
    ).toBe(false);
    expect(
      purgeConfirmationIsValid(purgePreview, "Case Sensitive Lot", " "),
    ).toBe(false);
    expect(
      purgeConfirmationIsValid(
        { ...purgePreview, eligible: false, blockers: [] },
        "Case Sensitive Lot",
        "duplicate receipt",
      ),
    ).toBe(false);
  });

  it("builds the locked preview payload without normalizing confirmation", () => {
    expect(
      lotPurgePayload(
        purgePreview,
        "Case Sensitive Lot",
        "  duplicate self-receipt  ",
      ),
    ).toEqual({
      confirmation_name: "Case Sensitive Lot",
      reason: "duplicate self-receipt",
      expected_version: 7,
      expected_graph_signature: "a".repeat(64),
    });
  });

  it("parses conflicts and resets confirmation while preserving reason", () => {
    const conflict = lotPurgeConflict({
      response: {
        status: 409,
        data: {
          detail: {
            code: "graph_changed",
            message: "Review the latest graph.",
            current_preview: {
              ...purgePreview,
              lot_version: 8,
              graph_signature: "b".repeat(64),
              eligible: false,
              blockers: [
                {
                  code: "active_boxes",
                  message: "Archive every box in the lot before purge.",
                  remediation: "Archive every box in the lot before purge.",
                  entities: [{ entity_type: "box", entity_id: 31 }],
                  entity_count: 1,
                  entities_truncated: false,
                },
              ],
            },
          },
        },
      },
    });
    expect(conflict?.code).toBe("graph_changed");
    expect(conflict?.current_preview?.lot_version).toBe(8);
    expect(conflict?.current_preview?.blockers[0].code).toBe("active_boxes");
    expect(purgeConflictFormState("keep this reason")).toEqual({
      confirmationName: "",
      reason: "keep this reason",
    });
    expect(lotPurgeConflict({ response: { status: 500 } })).toBeNull();
  });

  it("navigates only after cleanup is complete or unnecessary", () => {
    expect(purgeSuccessAction("completed")).toBe("navigate");
    expect(purgeSuccessAction("not_required")).toBe("navigate");
    for (const status of [
      "pending",
      "in_progress",
      "partial_failure",
      "failed",
    ] as const) {
      expect(purgeSuccessAction(status)).toBe("cleanup_required");
    }
    expect(purgeCleanupWarning(91, "partial_failure", 2)).toContain(
      "Purge audit #91",
    );
    expect(purgeCleanupWarning(91, "partial_failure", 2)).toContain(
      "2 stored objects",
    );
  });
});

const forcePreview: LotForcePurgePreview = {
  lot_id: 12,
  lot_name: "Case Sensitive Lot",
  lot_version: 7,
  confirmation_phrase: "FORCE DELETE LOT 12",
  active_box_ids: [29],
  active_box_count: 1,
  active_box_ids_truncated: false,
  archived_box_ids: [30],
  archived_box_count: 1,
  archived_box_ids_truncated: false,
  touched_request_ids: [44, 45],
  touched_request_count: 2,
  touched_request_ids_truncated: false,
  request_rewrites: [
    {
      request_id: 44,
      adjustment_event_type: "force_purge_adjusted",
      origin: "manual_entry",
      status: "completed",
      direction: "inbound",
      before_item_count: 2,
      after_item_count: 1,
      before_quantity: 2,
      after_quantity: 1,
      before_actual_received_quantity: 2,
      after_actual_received_quantity: 1,
      before_variance_quantity: 0,
      after_variance_quantity: 0,
      item_positions: [
        { item_id: 80, before_position: 1, after_position: null },
        { item_id: 81, before_position: 2, after_position: 1 },
      ],
      item_positions_truncated: false,
      removed_item_ids: [80],
      removed_item_count: 1,
      removed_item_ids_truncated: false,
      removed_discrepancy_ids: [90],
      removed_discrepancy_count: 1,
      removed_discrepancy_ids_truncated: false,
      removed_discrepancy_photo_ids: [91],
      removed_discrepancy_photo_count: 1,
      removed_discrepancy_photo_ids_truncated: false,
      preserved_sibling_lot_ids: [13],
      preserved_sibling_lot_count: 1,
      preserved_sibling_lot_ids_truncated: false,
    },
  ],
  request_rewrite_count: 1,
  request_rewrites_truncated: false,
  fully_deleted_request_ids: [45],
  fully_deleted_request_count: 1,
  fully_deleted_request_ids_truncated: false,
  incoming_lineage_detachments: [
    {
      request_id: 46,
      field_name: "parent_request_id",
      deleted_target_request_id: 45,
    },
  ],
  incoming_lineage_detachment_count: 1,
  incoming_lineage_detachments_truncated: false,
  object_cleanup: {
    deletable_key_count: 3,
    shared_skipped_key_count: 1,
  },
  hard_blockers: [],
  overridden_blockers: [
    {
      code: "active_boxes",
      message: "Active boxes are normally retained.",
      entity_ids: [29],
      entity_count: 1,
      entity_ids_truncated: false,
    },
    {
      code: "mixed_lot_receipt",
      message: "A request contains sibling Lot items.",
      entity_ids: [44],
      entity_count: 1,
      entity_ids_truncated: false,
    },
  ],
  graph_signature: "f".repeat(64),
  force_allowed: true,
  confirmation_policy: "exact_case_sensitive_no_normalization",
};

describe("administrative force lot purge", () => {
  it("offers escalation only to an admin after safe purge is blocked", () => {
    const blocked = {
      ...purgePreview,
      eligible: false,
      blockers: [
        {
          code: "active_boxes" as const,
          message: "Active boxes block safe purge.",
          remediation: "Archive every box.",
          entities: [],
          entity_count: 0,
          entities_truncated: false,
        },
      ],
    };
    expect(
      shouldOfferForcePurgeEscalation("admin", { id: 12 }, blocked),
    ).toBe(true);
    expect(
      shouldOfferForcePurgeEscalation("viewer", { id: 12 }, blocked),
    ).toBe(false);
    expect(
      shouldOfferForcePurgeEscalation(
        "admin",
        { id: 12, state: "merged" },
        blocked,
      ),
    ).toBe(false);
    expect(
      shouldOfferForcePurgeEscalation("admin", { id: 12 }, purgePreview),
    ).toBe(false);
  });

  it("recognizes non-overridable hard blocks", () => {
    expect(forcePurgeHasHardBlock(forcePreview)).toBe(false);
    expect(
      forcePurgeHasHardBlock({
        ...forcePreview,
        force_allowed: false,
        hard_blockers: [
          {
            code: "merge_target",
            message: "This Lot is a merge target.",
            entity_ids: [12],
            entity_count: 1,
            entity_ids_truncated: false,
          },
        ],
      }),
    ).toBe(true);
  });

  it("formats before-to-after impact and explicit sibling preservation", () => {
    expect(forcePurgeChange(4, 2)).toBe("4 → 2");
    expect(forcePurgeChange(null, null)).toBe("— → —");
    expect(
      forcePurgeSiblingPreservationText(forcePreview.request_rewrites[0]),
    ).toBe("1 sibling lot is preserved with the rewritten request.");
  });

  it("requires both exact confirmations, minimum reason, and exact acknowledgements", () => {
    const reason = "Verified duplicate graph requiring administrative removal";
    const codes = ["active_boxes", "mixed_lot_receipt"];
    expect(
      forcePurgeConfirmationIsValid(
        forcePreview,
        forcePreview.lot_name,
        forcePreview.confirmation_phrase,
        reason,
        codes,
      ),
    ).toBe(true);
    expect(
      forcePurgeConfirmationIsValid(
        forcePreview,
        forcePreview.lot_name.toLowerCase(),
        forcePreview.confirmation_phrase,
        reason,
        codes,
      ),
    ).toBe(false);
    expect(
      forcePurgeConfirmationIsValid(
        forcePreview,
        forcePreview.lot_name,
        "FORCE DELETE LOT 012",
        reason,
        codes,
      ),
    ).toBe(false);
    expect(
      forcePurgeConfirmationIsValid(
        forcePreview,
        forcePreview.lot_name,
        forcePreview.confirmation_phrase,
        "too short",
        codes,
      ),
    ).toBe(false);
    for (const invalidCodes of [
      ["active_boxes"],
      [...codes, "workflow_history"],
      [...codes, "active_boxes"],
    ]) {
      expect(
        forcePurgeConfirmationIsValid(
          forcePreview,
          forcePreview.lot_name,
          forcePreview.confirmation_phrase,
          reason,
          invalidCodes,
        ),
      ).toBe(false);
    }
  });

  it("builds the exact locked force payload", () => {
    expect(
      lotForcePurgePayload(
        forcePreview,
        forcePreview.lot_name,
        forcePreview.confirmation_phrase,
        "  Detailed reason for force deletion  ",
        ["mixed_lot_receipt", "active_boxes"],
      ),
    ).toEqual({
      confirmation_name: "Case Sensitive Lot",
      confirmation_phrase: "FORCE DELETE LOT 12",
      reason: "Detailed reason for force deletion",
      expected_version: 7,
      expected_graph_signature: "f".repeat(64),
      acknowledged_blocker_codes: ["active_boxes", "mixed_lot_receipt"],
    });
  });

  it("uses the current conflict preview and resets every safeguard except reason", () => {
    const current = {
      ...forcePreview,
      lot_version: 8,
      graph_signature: "e".repeat(64),
      force_allowed: false,
      hard_blockers: [
        {
          code: "invalid_identity" as const,
          message: "The active Lot has no canonical identity.",
          entity_ids: [12],
          entity_count: 1,
          entity_ids_truncated: false,
        },
      ],
    };
    const conflict = lotForcePurgeConflict({
      response: {
        status: 409,
        data: {
          detail: {
            code: "graph_changed",
            message: "Review the latest force impact.",
            current_preview: current,
          },
        },
      },
    });
    expect(conflict?.current_preview).toEqual(current);
    expect(
      forcePurgeConflictFormState(
        "Preserve this detailed administrative reason",
      ),
    ).toEqual({
      confirmationName: "",
      confirmationPhrase: "",
      reason: "Preserve this detailed administrative reason",
      acknowledgedBlockerCodes: [],
    });
    expect(lotForcePurgeConflict({ response: { status: 500 } })).toBeNull();
  });
});

