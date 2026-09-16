import { describe, expect, it } from "vitest";
import {
  ALERT_TYPE_STYLES,
  ALERT_TYPE_LABEL,
  canSendAlertTestEmail,
  formatAlertValueThreshold,
  NOTIFICATION_KIND_LABEL,
} from "@/pages/alertPresentation";

describe("reduced alert email policy presentation", () => {
  it("marks stuck-box history as retired without an active stuck label", () => {
    expect(ALERT_TYPE_LABEL.box_stuck).toBe("Retired legacy alert");
    expect(ALERT_TYPE_STYLES.box_stuck.label).toBe("Retired legacy alert");
    expect(formatAlertValueThreshold("box_stuck", 4, 30)).toContain(
      "historical snapshot",
    );
    expect(canSendAlertTestEmail("box_stuck")).toBe(false);
    expect(canSendAlertTestEmail("low_inventory")).toBe(true);
  });

  it("marks retired delivery kinds as legacy audit records", () => {
    expect(NOTIFICATION_KIND_LABEL.triggered).toBe("Opening email attempt");
    expect(NOTIFICATION_KIND_LABEL.reminder).toContain("Legacy");
    expect(NOTIFICATION_KIND_LABEL.escalated).toContain("Legacy");
    expect(NOTIFICATION_KIND_LABEL.resolved).toContain("Legacy");
  });
});
