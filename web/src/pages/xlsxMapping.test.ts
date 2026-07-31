import { describe, expect, it } from "vitest";
import {
  deliveryVariance,
  groupInboundItems,
  hasRequiredDiscrepancyReason,
} from "@/pages/xlsxMapping";

describe("Excel inbound row grouping", () => {
  it("merges repeated box numbers and combines distinct contents", () => {
    expect(
      groupInboundItems([
        { lot: "PR100", box_number: "1", contents: "Folder A" },
        { lot: "PR100", box_number: "001", contents: "Folder B" },
        { lot: "PR100", box_number: "1", contents: "Folder B" },
        { lot: "PR100", box_number: "2", contents: "Folder C" },
      ]),
    ).toEqual([
      {
        lot: "PR100",
        box_number: "001",
        contents: "Folder A | Folder B",
      },
      {
        lot: "PR100",
        box_number: "002",
        contents: "Folder C",
      },
    ]);
  });

  it("keeps the same box number separate when the lot differs", () => {
    expect(
      groupInboundItems([
        { lot: "PR100", box_number: "1", contents: "A" },
        { lot: "PR200", box_number: "1", contents: "B" },
      ]),
    ).toHaveLength(2);
  });
});

describe("delivery count variance", () => {
  it("calculates exact, short, and over deliveries", () => {
    expect(deliveryVariance(3, 3)).toBe(0);
    expect(deliveryVariance(3, 2)).toBe(-1);
    expect(deliveryVariance(3, 5)).toBe(2);
  });

  it("requires a non-blank reason only when counts differ", () => {
    expect(hasRequiredDiscrepancyReason(3, 3, "")).toBe(true);
    expect(hasRequiredDiscrepancyReason(3, 2, "")).toBe(false);
    expect(hasRequiredDiscrepancyReason(3, 4, "   ")).toBe(false);
    expect(
      hasRequiredDiscrepancyReason(3, 4, "Accepted an extra prepared box"),
    ).toBe(true);
  });
});
