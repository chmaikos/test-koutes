import { describe, expect, it } from "vitest";
import requestDetailSource from "@/pages/RequestDetailPage.tsx?raw";
import type {
  InboundCompletionPreview,
  InboundCompletionPreviewRow,
  InboundRequestItemInput,
} from "@/api/types";
import {
  canSubmitInboundCompletion,
  currentInboundCompletionPreview,
  inboundCompletionCounts,
  inboundCompletionFields,
  inboundCompletionFingerprint,
  inboundCompletionItems,
  inboundTargetPalletLabel,
  invalidateInboundCompletion,
  isStaleInboundImpactConflict,
  reviewedInboundCompletion,
  setInboundRelocationAcceptance,
} from "@/pages/inboundCompletion";

function preview(
  overrides: Partial<InboundCompletionPreview["summary"]> = {},
): InboundCompletionPreview {
  const summary = {
    created: 1,
    relocated: 2,
    blocked: 0,
    source_warehouse_counts: [
      { warehouse_id: 2, warehouse_name: "Source", count: 2 },
    ],
    ...overrides,
  };
  return {
    request_id: 10,
    request_version: 4,
    target_warehouse_id: 1,
    target_warehouse_name: "Target",
    impact_signature: "a".repeat(64),
    can_complete: summary.blocked === 0,
    summary,
    rows: [],
  };
}

const rows: InboundRequestItemInput[] = [
  {
    lot: " Lot A ",
    box_number: "1",
    pallet_number: " target ",
    contents: "Folder A",
  },
  {
    lot: "lot a",
    box_number: "001",
    pallet_number: "TARGET",
    contents: "Folder B",
  },
];

const fingerprintInput = {
  requestId: 10,
  requestVersion: 4,
  rows,
  lotConfirmations: {
    0: { id: 7, name: "Lot A" },
    1: { id: 7, name: "Lot A" },
  },
  palletConfirmations: {
    0: { id: 9, pallet_number: "TARGET" },
    1: { id: 9, pallet_number: "TARGET" },
  },
};

describe("inbound completion canonical payload", () => {
  it("uses one grouped payload for preview and completion", () => {
    expect(
      inboundCompletionItems(rows, fingerprintInput.palletConfirmations),
    ).toEqual({
      error: null,
      items: [
        {
          lot: "Lot A",
          box_number: "001",
          pallet_number: "TARGET",
          pallet_id: 9,
          contents: "Folder A | Folder B",
        },
      ],
    });
  });

  it("produces the same canonical payload after duplicate row reordering", () => {
    expect(
      inboundCompletionItems(
        [...rows].reverse(),
        {
          0: fingerprintInput.palletConfirmations[1],
          1: fingerprintInput.palletConfirmations[0],
        },
      ),
    ).toEqual(inboundCompletionItems(rows, fingerprintInput.palletConfirmations));
  });

  it("rejects conflicting confirmed pallet identities", () => {
    expect(
      inboundCompletionItems(rows, {
        ...fingerprintInput.palletConfirmations,
        1: { id: 10, pallet_number: "TARGET" },
      }).error,
    ).toMatch(/different pallets/i);
  });

  it("keeps blank pallet rows as canonical null without confirmation", () => {
    expect(
      inboundCompletionItems(
        [{ lot: " Lot B ", box_number: "2", pallet_number: "   " }],
        {},
      ),
    ).toEqual({
      error: null,
      items: [
        {
          lot: "Lot B",
          box_number: "002",
          pallet_number: null,
          contents: undefined,
        },
      ],
    });
  });
});

describe("inbound impact fingerprint and state", () => {
  it("is deterministic and changes for rows, confirmations, and request version", () => {
    const first = inboundCompletionFingerprint(fingerprintInput);
    expect(inboundCompletionFingerprint(fingerprintInput)).toBe(first);
    expect(
      inboundCompletionFingerprint({
        ...fingerprintInput,
        rows: rows.map((row, index) =>
          index === 0 ? { ...row, contents: "Changed" } : row,
        ),
      }),
    ).not.toBe(first);
    expect(
      inboundCompletionFingerprint({
        ...fingerprintInput,
        palletConfirmations: {
          ...fingerprintInput.palletConfirmations,
          0: { id: 11, pallet_number: "TARGET" },
        },
      }),
    ).not.toBe(first);
    expect(
      inboundCompletionFingerprint({
        ...fingerprintInput,
        requestVersion: 5,
      }),
    ).not.toBe(first);
  });

  it("starts relocation acceptance off and invalidates stale reviews", () => {
    const fingerprint = inboundCompletionFingerprint(fingerprintInput);
    const reviewed = reviewedInboundCompletion(preview(), fingerprint);
    expect(reviewed.acceptRelocations).toBe(false);
    expect(currentInboundCompletionPreview(reviewed, fingerprint, 4)).toBe(
      reviewed.preview,
    );
    expect(
      currentInboundCompletionPreview(reviewed, `${fingerprint}x`, 4),
    ).toBeNull();
    expect(currentInboundCompletionPreview(reviewed, fingerprint, 5)).toBeNull();

    const accepted = setInboundRelocationAcceptance(reviewed, true);
    expect(accepted.acceptRelocations).toBe(true);
    expect(
      invalidateInboundCompletion("Inventory changed; review again"),
    ).toEqual({
      preview: null,
      fingerprint: null,
      acceptRelocations: false,
      notice: "Inventory changed; review again",
    });
  });

  it("fingerprints blank and null pallet values identically", () => {
    const base = {
      requestId: 1,
      requestVersion: 1,
      lotConfirmations: { 0: { id: 2, name: "Lot" } },
      palletConfirmations: {},
    };
    expect(
      inboundCompletionFingerprint({
        ...base,
        rows: [{ lot: "Lot", box_number: "1", pallet_number: "" }],
      }),
    ).toBe(
      inboundCompletionFingerprint({
        ...base,
        rows: [{ lot: "Lot", box_number: "1", pallet_number: null }],
      }),
    );
  });
});

describe("inbound pallet target labels", () => {
  const row = {
    current_pallet_number: null,
    target_pallet_resolution: {
      resolution: "unassigned",
      pallet_id: null,
      pallet_number: null,
    },
  } as InboundCompletionPreviewRow;

  it("renders unassigned, preserved, and explicit target resolutions", () => {
    expect(inboundTargetPalletLabel(row)).toBe("Unassigned");
    expect(
      inboundTargetPalletLabel({
        ...row,
        current_pallet_number: "PAL-1",
        target_pallet_resolution: {
          resolution: "preserve_existing",
          pallet_id: 2,
          pallet_number: "PAL-1",
        },
      }),
    ).toBe("Keep current pallet PAL-1");
    expect(
      inboundTargetPalletLabel({
        ...row,
        target_pallet_resolution: {
          resolution: "preserve_existing",
          pallet_id: null,
          pallet_number: null,
        },
      }),
    ).toBe("Remain Unassigned");
    expect(
      inboundTargetPalletLabel({
        ...row,
        target_pallet_resolution: {
          resolution: "existing",
          pallet_id: 3,
          pallet_number: "PAL-2",
        },
      }),
    ).toBe("PAL-2");
  });
});

describe("inbound completion gating and payload", () => {
  it("allows all-new previews without a relocation checkbox", () => {
    const allNew = preview({ created: 3, relocated: 0 });
    expect(canSubmitInboundCompletion(allNew, false)).toBe(true);
    expect(inboundCompletionFields(allNew, false)).toEqual({
      inbound_impact_signature: "a".repeat(64),
      accept_existing_received_boxes: false,
    });
  });

  it("gates relocations and accepts every eligible match with one flag", () => {
    const mixed = preview();
    expect(canSubmitInboundCompletion(mixed, false)).toBe(false);
    expect(canSubmitInboundCompletion(mixed, true)).toBe(true);
    expect(inboundCompletionFields(mixed, true)).toEqual({
      inbound_impact_signature: "a".repeat(64),
      accept_existing_received_boxes: true,
    });
  });

  it("blocks completion whenever the preview has blocked rows", () => {
    const blocked = preview({ blocked: 1 });
    expect(canSubmitInboundCompletion(blocked, true)).toBe(false);
  });

  it("computes accepted totals and ordered variance", () => {
    expect(inboundCompletionCounts(4, preview(), false)).toEqual({
      created: 1,
      relocated: 2,
      blocked: 0,
      eligible: 3,
      accepted: 1,
      variance: -1,
    });
    expect(inboundCompletionCounts(2, preview(), true)).toEqual({
      created: 1,
      relocated: 2,
      blocked: 0,
      eligible: 3,
      accepted: 3,
      variance: 1,
    });
  });
});

describe("stale inbound impact conflicts", () => {
  it("recognizes only the completion impact conflict", () => {
    expect(
      isStaleInboundImpactConflict({
        response: {
          status: 409,
          data: {
            detail: {
              message:
                "inbound impact changed; refresh the preview and retry",
            },
          },
        },
      }),
    ).toBe(true);
    expect(
      isStaleInboundImpactConflict({
        response: {
          status: 409,
          data: { detail: { message: "request changed; refresh and retry" } },
        },
      }),
    ).toBe(true);
    expect(
      isStaleInboundImpactConflict({
        response: { status: 400, data: { detail: "inbound impact changed" } },
      }),
    ).toBe(false);
  });

  it("keeps cross-warehouse pallet identity separate from box relocation", () => {
    expect(requestDetailSource).toContain(
      "keeping\n                the same pallet across source and target warehouses is valid",
    );
    expect(requestDetailSource).not.toContain(
      "target pallet belongs to another warehouse",
    );
  });
});
