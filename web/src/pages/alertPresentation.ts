import { AlertTriangle } from "lucide-react";
import type {
  AlertNotificationKind,
  AlertType,
} from "@/api/types";

interface TypeStyle {
  label: string;
  icon: typeof AlertTriangle;
  iconClass: string;
}

export const ALERT_TYPE_STYLES: Record<AlertType, TypeStyle> = {
  low_inventory: {
    label: "Low inventory",
    icon: AlertTriangle,
    iconClass: "text-rose-500",
  },
  max_capacity: {
    label: "Max capacity",
    icon: AlertTriangle,
    iconClass: "text-rose-500",
  },
  near_capacity: {
    label: "Near capacity",
    icon: AlertTriangle,
    iconClass: "text-amber-500",
  },
  near_low_inventory: {
    label: "Near low inventory",
    icon: AlertTriangle,
    iconClass: "text-amber-500",
  },
  box_stuck: {
    label: "Retired legacy alert",
    icon: AlertTriangle,
    iconClass: "text-slate-400",
  },
};

export const ALERT_TYPE_LABEL: Record<AlertType, string> = {
  low_inventory: "Low inventory",
  max_capacity: "Max capacity",
  near_capacity: "Near capacity",
  near_low_inventory: "Near low inventory",
  box_stuck: "Retired legacy alert",
};

export const NOTIFICATION_KIND_LABEL: Record<
  AlertNotificationKind,
  string
> = {
  triggered: "Opening email attempt",
  reminder: "Legacy reminder record",
  escalated: "Legacy escalation record",
  resolved: "Legacy resolution record",
  test: "Test email",
};

export function formatAlertValueThreshold(
  type: AlertType,
  value: number,
  threshold: number,
): string {
  if (type === "box_stuck") {
    return `${value} / ${threshold} (historical snapshot)`;
  }
  return `${value} / ${threshold}`;
}

export function canSendAlertTestEmail(alertType: AlertType): boolean {
  return alertType !== "box_stuck";
}
