export type Role = "admin" | "operator" | "viewer";

export type BoxStatus =
  | "received"
  | "processing"
  | "incomplete"
  | "ready_to_return"
  | "returned";

// Ordered to match the API's linear status chain so dropdowns and other
// "advance to next status" affordances render in the natural sequence.
export const ALL_BOX_STATUSES: BoxStatus[] = [
  "received",
  "processing",
  "incomplete",
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

export interface Performer {
  employee_id: number;
  employee_name: string;
  pages: number;
  hours: number;
  pages_per_day: number;
}

export interface WarehouseProductivity {
  warehouse_id: number;
  total_pages: number;
  total_hours: number;
  avg_pages_per_day: number;
  entry_count: number;
  active_employees: number;
  top: Performer[];
  bottom: Performer[];
}

export interface ProductivitySummary {
  period_start: string;
  period_end: string;
  warehouses: WarehouseProductivity[];
  total_pages: number;
  total_hours: number;
  avg_pages_per_day: number;
}

export interface Employee {
  id: number;
  warehouse_id: number;
  full_name: string;
  default_hours_per_day: string;
  is_active: boolean;
  excluded_from_metrics: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProductivityEntry {
  id: number;
  employee_id: number;
  warehouse_id: number;
  entry_date: string;
  pages: number;
  hours_worked: string;
  note: string | null;
  excluded_from_metrics: boolean;
  created_by_user_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface WarehouseSummary {
  warehouse_id: number;
  name: string;
  min_inventory: number;
  max_capacity: number;
  // Total in-warehouse boxes (everything but `returned`). Kept for
  // back-compat with older bundles; the dashboard renders
  // available/unavailable separately now.
  inventory: number;
  // Two-bucket split that the dashboard cards read directly:
  //   available   = received + processing  (vs `min_inventory`)
  //   unavailable = incomplete + ready_to_return (vs `max_capacity`)
  available_boxes: number;
  unavailable_boxes: number;
  // Subset of `unavailable_boxes`: how many boxes are physically
  // packaged for return right now.
  ready_to_return_boxes: number;
  received_today: number;
  returned_today: number;
  // Boxes that left `processing` today (into incomplete or
  // ready_to_return) and projected completions per full calendar day.
  completed_today: number;
  completed_per_day: number;
  counts_by_status: Record<BoxStatus, number>;
  open_alerts: number;
  productivity_today: WarehouseProductivity | null;
  productivity_week: WarehouseProductivity | null;
}

export interface DashboardSummary {
  warehouses: WarehouseSummary[];
  total_active_boxes: number;
  total_available_boxes: number;
  total_unavailable_boxes: number;
  total_completed_today: number;
  total_open_alerts: number;
}

// Sortable column keys exposed by ``GET /boxes`` (mirrors
// ``SORTABLE_FIELDS`` in ``api/app/routers/_filters.py``). The
// ``BoxesPage`` clickable headers cycle through these.
export type BoxSortField =
  | "box_number"
  | "lot"
  | "status"
  | "warehouse"
  | "received_at"
  | "updated_at";

export interface BoxFilters {
  warehouse_id?: number;
  status?: BoxStatus;
  lot?: string;
  search?: string;
  received_from?: string;
  received_to?: string;
  updated_from?: string;
  updated_to?: string;
  sort_by?: BoxSortField;
  sort_dir?: "asc" | "desc";
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
