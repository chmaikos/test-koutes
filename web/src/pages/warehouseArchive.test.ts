import { describe, expect, it } from "vitest";
import { warehouseArchiveError } from "./warehouseArchive";

describe("warehouse archive conflicts", () => {
  it("formats every API blocker", () => {
    const error = {
      response: {
        data: {
          detail: {
            message: "blocked",
            active_boxes: 2,
            active_requests: 1,
            last_active_warehouse: true,
          },
        },
      },
    };

    expect(warehouseArchiveError(error)).toBe(
      "Cannot archive: 2 active boxes, 1 open request, it is the last active warehouse.",
    );
  });

  it("preserves plain API details", () => {
    expect(
      warehouseArchiveError({
        response: { data: { detail: "warehouse is already archived" } },
      }),
    ).toBe("warehouse is already archived");
  });
});
