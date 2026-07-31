export type Role = "admin" | "warehouse_mover" | "operator" | "viewer";

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
  | "returned"
  | "archived"
  | "restored";

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
  min_pages_per_day: number | null;
  is_active: boolean;
  archived_at: string | null;
  archived_by_user_id: number | null;
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
  archived_at: string | null;
  archived_by_user_id: number | null;
  archive_reason: string | null;
  created_at: string;
  updated_at: string;
  receipt_request_id: number | null;
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

export interface EmployeePeriodAverage {
  period_start: string;
  period_end: string;
  total_pages: number;
  total_hours: number;
  entry_count: number;
  pages_per_day: number | null;
  below_minimum: boolean | null;
}

export interface EmployeeAverage {
  employee_id: number;
  employee_name: string;
  excluded_from_metrics: boolean;
  weekly: EmployeePeriodAverage;
  monthly: EmployeePeriodAverage;
  three_month: EmployeePeriodAverage;
  consistently_below_minimum: boolean;
}

export interface EmployeeAverages {
  warehouse_id: number;
  anchor_date: string;
  min_pages_per_day: number | null;
  employees: EmployeeAverage[];
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

export interface EmployeeImportItem {
  source_row: number;
  full_name: string;
  default_hours_per_day?: number;
  is_active?: boolean;
  excluded_from_metrics?: boolean;
}

export interface EmployeeImportSkip {
  row: number;
  full_name: string;
  reason: string;
}

export interface EmployeeImportResult {
  created: Employee[];
  updated: Employee[];
  skipped: EmployeeImportSkip[];
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
  force?: boolean;
}

export interface BulkSkip {
  box_id: number;
  box_number: string;
  reason: string;
}

export interface BulkResult {
  updated: Box[];
  skipped: BulkSkip[];
  cancelled_request_ids: number[];
}

export interface ImportSkip {
  row: number;
  box_number: string | null;
  reason: string;
}

export interface ImportResult {
  created: Box[];
  restored: Box[];
  skipped: ImportSkip[];
  receipt_request_ids: number[];
}

export interface BulkDeleteResult {
  deleted_ids: number[];
  archived_ids: number[];
  cancelled_request_ids: number[];
  skipped: BulkSkip[];
}

export interface BoxDeleteResult {
  box_id: number;
  archived: boolean;
  cancelled_request_ids: number[];
}

export type RequestDirection = "inbound" | "return";
export type RequestOrigin =
  | "workflow"
  | "xlsx_import"
  | "manual_entry"
  | "legacy_backfill";

export type RequestStatus =
  | "submitted"
  | "approved"
  | "in_transit"
  | "completed"
  | "rejected"
  | "cancelled";

export type RequestDocumentType =
  | "delivery_note"
  | "return_note"
  | "other";

export interface BoxRequestItem {
  id: number;
  box_id: number | null;
  lot: string | null;
  box_number: string | null;
  contents: string | null;
}

export interface RequestDocument {
  id: number;
  document_type: RequestDocumentType;
  erp_reference: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  uploaded_by_user_id: number | null;
  created_at: string;
  is_current: boolean;
}

export interface BoxRequest {
  id: number;
  direction: RequestDirection;
  warehouse_id: number;
  quantity: number;
  status: RequestStatus;
  requester_user_id: number | null;
  requester_name: string;
  source_inbound_request_id: number | null;
  origin: RequestOrigin;
  suggestion_quantity: number;
  current_available: number;
  min_inventory: number;
  pending_inbound: number;
  eligible_return: number;
  actual_received_quantity: number | null;
  variance_quantity: number | null;
  rejection_reason: string | null;
  cancellation_reason: string | null;
  discrepancy_reason: string | null;
  submitted_at: string;
  approved_at: string | null;
  in_transit_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
  items: BoxRequestItem[];
  documents: RequestDocument[];
}

export interface RequestEvent {
  id: number;
  event_type: string;
  from_status: RequestStatus | null;
  to_status: RequestStatus | null;
  user_id: number | null;
  user_name: string | null;
  note: string | null;
  occurred_at: string;
}

export interface RequestSuggestion {
  direction: RequestDirection;
  warehouse_id: number;
  current_available: number;
  min_inventory: number;
  pending_inbound: number;
  suggested_quantity: number;
  eligible_return: number;
}

export interface ReturnSource {
  id: number;
  warehouse_id: number;
  completed_at: string;
  origin: RequestOrigin;
  delivered_quantity: number;
  eligible_quantity: number;
}

export interface ReturnCandidate {
  box_id: number;
  box_number: string;
  lot: string;
  contents: string | null;
  status: "ready_to_return";
}

export interface RequestFilters {
  warehouse_id?: number;
  direction?: RequestDirection;
  status?: RequestStatus;
}

export interface InboundRequestItemInput {
  lot: string;
  box_number: string;
  contents?: string;
}

export interface XlsxPreviewRow {
  row_number: number;
  cells: string[];
}

export interface XlsxPreviewSheet {
  name: string;
  max_columns: number;
  rows: XlsxPreviewRow[];
}

export interface XlsxPreview {
  filename: string;
  sheets: XlsxPreviewSheet[];
}
