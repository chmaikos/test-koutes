import { describe, expect, it } from "vitest";
import {
  deleteActionLabel,
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
});
