import { describe, expect, it } from "vitest";
import {
  canRelocateReturnedSelection,
  deleteActionLabel,
  formatCancelledRequestIds,
  hasRequiredOverrideReason,
  shouldOfferForceArchive,
} from "@/pages/boxIntegrity";

describe("box integrity actions", () => {
  it("requires a non-blank reason for force actions", () => {
    expect(hasRequiredOverrideReason(false, "")).toBe(true);
    expect(hasRequiredOverrideReason(true, "")).toBe(false);
    expect(hasRequiredOverrideReason(true, "   ")).toBe(false);
    expect(hasRequiredOverrideReason(true, "Physical correction")).toBe(true);
  });

  it("labels force deletion as an archive-capable action", () => {
    expect(deleteActionLabel(false)).toBe("Delete unlinked boxes");
    expect(deleteActionLabel(true)).toBe("Delete or archive");
  });

  it("offers force archive only after a linked-box conflict", () => {
    expect(shouldOfferForceArchive(409)).toBe(true);
    expect(shouldOfferForceArchive(403)).toBe(false);
    expect(shouldOfferForceArchive(500)).toBe(false);
    expect(shouldOfferForceArchive(undefined)).toBe(false);
  });

  it("requires an audited admin override to relocate returned selections", () => {
    expect(canRelocateReturnedSelection(1, true, false, false, "")).toBe(true);
    expect(canRelocateReturnedSelection(1, true, true, false, "Reason")).toBe(
      false,
    );
    expect(canRelocateReturnedSelection(1, true, true, true, "   ")).toBe(false);
    expect(canRelocateReturnedSelection(1, true, true, true, "Reason")).toBe(
      true,
    );
    expect(canRelocateReturnedSelection(0, true, false, false, "")).toBe(true);
  });

  it("formats exact cancelled request IDs", () => {
    expect(formatCancelledRequestIds([7, 11])).toBe("#7, #11");
  });
});
