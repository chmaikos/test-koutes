export type Role = "admin" | "operator" | "viewer";

export type BoxStatus = "received" | "ready_to_return" | "returned";

export const ALL_BOX_STATUSES: BoxStatus[] = [
  "received",
  "ready_to_return",
  "returned",
];

export type AlertType =
  | "low_inventory"
  | "max_capacity"
  | "near_capacity"
  | "near_low_inventory"
  | "box_stuck";

export type AlertNotificationKind =
  | "triggered"
  | "reminder"
  | "escalated"
  | "resolved"
  | "test";

export type BoxEventType =
  | "created"
  | "moved"
  | "status_changed"
  | "returned";

export interface User {
  id: number;
  entra_oid: string | null;
  email: string;
  display_name: string;
  role: Role;
  role_override: boolean;
  is_active: boolean;
  email_alerts_enabled: boolean;
  last_login_at: string | null;
  username: string | null;
  is_local: boolean;
  must_change_credentials: boolean;
  warehouse_ids: number[];
}

export interface LocalLoginResponse {
  access_token: string;
  token_type: string;
  expires_in_minutes: number;
  must_change_credentials: boolean;
  user: User;
}

export interface Warehouse {
  id: number;
  name: string;
  min_inventory: number;
  max_capacity: number;
}

export interface Box {
  id: number;
  box_number: string;
  lot: string;
  contents: string | null;
  current_warehouse_id: number;
  status: BoxStatus;
  received_at: string | null;
  processing_completed_at: string | null;
  returned_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface BoxEvent {
  id: number;
  box_id: number;
  warehouse_id: number;
  event_type: BoxEventType;
  from_status: BoxStatus | null;
  to_status: BoxStatus | null;
  from_warehouse_id: number | null;
  to_warehouse_id: number | null;
  occurred_at: string;
  user_id: number | null;
  note: string | null;
}

export interface Alert {
  id: number;
  warehouse_id: number;
  type: AlertType;
  threshold: number;
  value: number;
  triggered_at: string;
  notified_at: string | null;
  resolved_at: string | null;
  acknowledged_at: string | null;
  escalated_at: string | null;
}

export interface AlertNotification {
  id: number;
  alert_id: number;
  kind: AlertNotificationKind;
  sent_at: string;
  recipients: string;
  ok: boolean;
  error: string | null;
}

export interface AlertDetail {
  alert: Alert;
  notifications: AlertNotification[];
}

export interface AlertRecipientsRow {
  warehouse_id: number;
  warehouse_name: string;
  primary: string[];
}

export interface AlertRecipients {
  warehouses: AlertRecipientsRow[];
  escalation: string[];
}

export interface AlertTestEmailResult {
  ok: boolean;
  error: string | null;
  recipients: string[];
  subject: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface WarehouseSummary {
  warehouse_id: number;
  name: string;
  min_inventory: number;
  max_capacity: number;
  inventory: number;
  received_today: number;
  returned_today: number;
  counts_by_status: Record<BoxStatus, number>;
  open_alerts: number;
}

export interface DashboardSummary {
  warehouses: WarehouseSummary[];
  total_active_boxes: number;
  total_open_alerts: number;
}

export interface BoxFilters {
  warehouse_id?: number;
  status?: BoxStatus;
  lot?: string;
  search?: string;
  received_from?: string;
  received_to?: string;
  updated_from?: string;
  updated_to?: string;
}

export interface BulkBoxUpdate {
  box_ids: number[];
  warehouse_id?: number;
  status?: BoxStatus;
  note?: string;
}

export interface BulkSkip {
  box_id: number;
  box_number: string;
  reason: string;
}

export interface BulkResult {
  updated: Box[];
  skipped: BulkSkip[];
}

export interface ImportSkip {
  row: number;
  box_number: string | null;
  reason: string;
}

export interface ImportResult {
  created: Box[];
  skipped: ImportSkip[];
}

export interface BulkDeleteResult {
  deleted_ids: number[];
  skipped: BulkSkip[];
}
