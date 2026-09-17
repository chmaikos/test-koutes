export type Role = "admin" | "warehouse_mover" | "operator" | "viewer";

export type BoxStatus =
  | "quarantined"
  | "received"
  | "processing"
  | "incomplete"
  | "ready_to_return"
  | "returned";

// Ordered to match the API's linear status chain so dropdowns and other
// "advance to next status" affordances render in the natural sequence.
export const ALL_BOX_STATUSES: BoxStatus[] = [
  "quarantined",
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
  | "restored"
  | "lot_reassigned"
  | "pallet_assigned"
  | "pallet_unassigned";

export interface User {
  id: number;
  entra_oid: string | null;
  email: string;
  display_name: string;
  role: Role;
  role_override: boolean;
  is_active: boolean;
  email_alerts_enabled: boolean;
  email_requests_enabled: boolean;
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
  lead_time_days: number;
  safety_stock_percent: number;
  history_30_weight: number;
  history_90_weight: number;
  forecast_adjustment: number | null;
  receipt_mode: "auto_complete" | "admin_review";
  require_erp_document: boolean;
  quarantine_imports: boolean;
  quarantine_manual_receipts: boolean;
  two_person_approval_threshold: number | null;
  is_active: boolean;
  archived_at: string | null;
  archived_by_user_id: number | null;
}

export interface Box {
  id: number;
  box_number: string;
  lot: string;
  lot_id: number;
  pallet_id: number | null;
  pallet_number: string | null;
  contents: string | null;
  file_count: number;
  active_file_count: number;
  archived_file_count: number;
  files: FileSummary[];
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

export interface BoxUpdateResult extends Box {
  cancelled_request_ids: number[];
  detached_pallet_id: number | null;
}

export interface StagedReceiptResult {
  outcome: "staged";
  staged_receipt_id: number;
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
  metadata: Record<string, unknown>;
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
  quarantined_boxes: number;
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
  lot_id?: number;
  pallet_id?: number;
  unassigned_pallet?: boolean;
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
  staged_receipt_ids: number[];
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
  | "backorder"
  | "return_reselection"
  | "xlsx_import"
  | "manual_entry"
  | "legacy_backfill";

export type RequestStatus =
  | "draft"
  | "submitted"
  | "approved"
  | "preparing"
  | "ready_for_transport"
  | "in_transit"
  | "awaiting_confirmation"
  | "completed"
  | "rejected"
  | "cancelled";

export type RequestDocumentType =
  | "delivery_note"
  | "return_note"
  | "other";
export type RequestPriority = "low" | "normal" | "high" | "urgent";
export type RequestQueue =
  | "unassigned"
  | "due_today"
  | "overdue"
  | "ready_for_transport"
  | "awaiting_confirmation"
  | "pending_receipt_review";
export type RequestSortField =
  | "created_at"
  | "updated_at"
  | "priority"
  | "requested_date"
  | "sla_deadline"
  | "scheduled_window_start"
  | "status"
  | "assignment";

export interface BoxRequestItem {
  id: number;
  box_id: number | null;
  lot_id: number | null;
  lot: string | null;
  pallet_id: number | null;
  pallet: string | null;
  pallet_number: string | null;
  box_number: string | null;
  contents: string | null;
  files: RequestFileSnapshot[];
}

export interface FileInput {
  reference: string;
  description?: string | null;
  barcode?: string | null;
}

export interface FileSummary extends FileInput {
  id: number;
  position: number;
  version: number;
}

export type FileActivity = "active" | "archived" | "all";
export type FileSortField =
  | "reference"
  | "lot"
  | "pallet"
  | "box"
  | "warehouse"
  | "status"
  | "position"
  | "created_at"
  | "updated_at";

export interface TrackedFile extends FileSummary {
  lot_id: number;
  lot: string;
  pallet_id: number | null;
  pallet: string | null;
  box_id: number;
  box: string;
  warehouse_id: number;
  warehouse: string;
  status: BoxStatus;
  is_active: boolean;
  archived_at: string | null;
  archived_by_user_id: number | null;
  archive_reason: string | null;
  created_at: string;
  updated_at: string;
  created_by_user_id: number | null;
  updated_by_user_id: number | null;
}

export interface TrackedFileFilters {
  search?: string;
  warehouse_id?: number;
  lot_id?: number;
  pallet_id?: number;
  box_id?: number;
  status?: BoxStatus;
  activity?: FileActivity;
  include_inactive?: boolean;
  sort_by?: FileSortField;
  sort_dir?: "asc" | "desc";
}

export interface FileCreatePayload extends FileInput {
  box_id: number;
  position?: number;
}

export interface FileUpdatePayload extends Partial<FileInput> {
  expected_version: number;
  force?: boolean;
  reason?: string;
}

export interface FileMovePayload {
  box_id: number;
  position?: number;
  expected_version: number;
  force?: boolean;
  reason?: string;
}

export interface FileStateChangePayload {
  expected_version: number;
  position?: number;
  reason: string;
  force?: boolean;
}

export interface FileExportFilters extends TrackedFileFilters {}

export interface TrackedFileEvent {
  id: number;
  file_id: number;
  event_type:
    | "created"
    | "updated"
    | "moved"
    | "archived"
    | "restored"
    | "box_moved"
    | "box_status_changed"
    | "lot_reassigned";
  before_snapshot: Record<string, unknown> | null;
  after_snapshot: Record<string, unknown> | null;
  actor_user_id: number | null;
  reason: string | null;
  occurred_at: string;
  metadata: Record<string, unknown>;
}

export interface FileIntegrityIdGroup {
  count: number;
  file_ids: number[];
  snapshot_ids: number[];
}

export interface FileIntegrity {
  safe: boolean;
  conflict_count: number;
  cross_lot_placements: FileIntegrityIdGroup;
  duplicate_normalized_references: {
    count: number;
    groups: Array<{
      lot_id: number;
      normalized_reference: string;
      file_ids: number[];
    }>;
  };
  invalid_positions: FileIntegrityIdGroup;
  archived_files_in_active_workflows: FileIntegrityIdGroup;
  detached_tracked_snapshots: FileIntegrityIdGroup;
}

export interface RequestFileSnapshot extends FileInput {
  id: number;
  file_id: number | null;
  position: number;
  snapshot_kind: "tracked_file" | "legacy_contents";
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

export interface RequestAttachment {
  id: number;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  uploaded_by_user_id: number | null;
  created_at: string;
}

export interface RequestComment {
  id: number;
  author_user_id: number | null;
  author_name: string;
  body: string;
  created_at: string;
}

export interface RequestAssignee {
  id: number;
  display_name: string;
  email: string;
  role: "admin" | "warehouse_mover";
}

export type RequestDiscrepancyType =
  | "missing"
  | "unexpected"
  | "damaged"
  | "wrong_lot"
  | "wrong_contents"
  | "rejected";

export interface RequestDiscrepancyPhoto {
  id: number;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  uploaded_by_user_id: number | null;
  created_at: string;
}

export interface RequestDiscrepancy {
  id: number;
  request_item_id: number | null;
  box_id: number | null;
  discrepancy_type: RequestDiscrepancyType;
  quantity: number | null;
  notes: string | null;
  created_by_user_id: number | null;
  created_at: string;
  photos: RequestDiscrepancyPhoto[];
}

export interface RequestDiscrepancyInput {
  discrepancy_type: RequestDiscrepancyType;
  request_item_id?: number;
  box_id?: number;
  quantity?: number;
  notes?: string;
}

export type RequestExceptionKind =
  | "hold"
  | "reschedule"
  | "failed_delivery";

export interface RequestException {
  id: number;
  exception_kind: RequestExceptionKind;
  reason: string;
  revised_window_start: string | null;
  revised_window_end: string | null;
  resume_target: RequestStatus;
  created_by_user_id: number | null;
  created_at: string;
  resolved_by_user_id: number | null;
  resolved_at: string | null;
  resolution: string | null;
}

export interface BoxRequest {
  id: number;
  direction: RequestDirection;
  warehouse_id: number;
  target_warehouse_id: number | null;
  quantity: number;
  status: RequestStatus;
  requester_user_id: number | null;
  requester_name: string;
  priority: RequestPriority;
  requested_date: string | null;
  scheduled_window_start: string | null;
  scheduled_window_end: string | null;
  sla_deadline: string | null;
  assigned_mover_user_id: number | null;
  assigned_mover_name: string | null;
  destination_contact: string | null;
  internal_location: string | null;
  special_handling_instructions: string | null;
  source_inbound_request_id: number | null;
  parent_request_id: number | null;
  root_request_id: number | null;
  child_request_ids: number[];
  origin: RequestOrigin;
  suggestion_quantity: number;
  current_available: number;
  min_inventory: number;
  pending_inbound: number;
  eligible_return: number;
  recommendation_snapshot?: RequestSuggestion | null;
  actual_received_quantity: number | null;
  variance_quantity: number | null;
  rejection_reason: string | null;
  cancellation_reason: string | null;
  discrepancy_reason: string | null;
  receipt_restore_archived: boolean;
  receipt_quarantine: boolean;
  receipt_document_required: boolean;
  submitted_at: string;
  approved_at: string | null;
  approved_by_user_id: number | null;
  preparing_at: string | null;
  preparing_by_user_id: number | null;
  ready_for_transport_at: string | null;
  ready_for_transport_by_user_id: number | null;
  in_transit_at: string | null;
  in_transit_by_user_id: number | null;
  awaiting_confirmation_at: string | null;
  awaiting_confirmation_by_user_id: number | null;
  completed_at: string | null;
  completed_by_user_id: number | null;
  created_at: string;
  updated_at: string;
  version: number;
  items: BoxRequestItem[];
  documents: RequestDocument[];
  discrepancies: RequestDiscrepancy[];
  comments: RequestComment[];
  attachments: RequestAttachment[];
  operational_exceptions: RequestException[];
  current_exception: RequestException | null;
  permissions: {
    can_assign: boolean;
    can_schedule: boolean;
    can_comment: boolean;
    can_attach: boolean;
    can_prepare: boolean;
    can_mark_ready: boolean;
    can_start_transit: boolean;
    can_mark_arrived: boolean;
    can_confirm: boolean;
    can_hold: boolean;
    can_resume: boolean;
    can_reschedule: boolean;
    can_report_failed: boolean;
    can_retry: boolean;
  };
}

interface CreateBoxRequestBase {
  warehouse_id: number;
  quantity: number;
  priority?: BoxRequest["priority"];
  requested_date?: string;
  sla_deadline?: string;
  destination_contact?: string;
  internal_location?: string;
  special_handling_instructions?: string;
}

export type CreateBoxRequestInput =
  | (CreateBoxRequestBase & {
      direction: "inbound";
      target_warehouse_id?: never;
    })
  | (CreateBoxRequestBase & {
      direction: "return";
      target_warehouse_id: number;
      source_inbound_request_id: number;
      box_ids: number[];
    });

export interface RequestEvent {
  id: number;
  event_type: string;
  from_status: RequestStatus | null;
  to_status: RequestStatus | null;
  user_id: number | null;
  user_name: string | null;
  note: string | null;
  metadata: Record<string, unknown>;
  occurred_at: string;
}

export type RequestIssueSeverity = "critical" | "high" | "medium" | "low";

export interface RequestReconciliationIssue {
  issue_key: string;
  issue_type: string;
  severity: RequestIssueSeverity;
  request_id: number;
  box_id: number | null;
  pallet_id: number | null;
  pallet_number: string | null;
  warehouse_id: number;
  warehouse_name: string;
  target_warehouse_id: number | null;
  target_warehouse_name: string | null;
  assigned_mover_user_id: number | null;
  assigned_mover_name: string | null;
  request_status: RequestStatus;
  title: string;
  detail: string;
  occurred_at: string;
  due_at: string | null;
  request_path: string;
  box_path: string | null;
}

export interface RequestReconciliation {
  items: RequestReconciliationIssue[];
  total: number;
  page: number;
  page_size: number;
  generated_at: string;
  summary: {
    total: number;
    critical: number;
    high: number;
    medium: number;
    low: number;
    by_type: Record<string, number>;
  };
}

export interface RequestDurationMetric {
  supported: boolean;
  average_seconds: number | null;
  sample_size: number;
}

export interface RequestRateMetric {
  numerator: number;
  denominator: number;
  rate: number | null;
}

export interface RequestThroughput {
  id: number | null;
  name: string;
  completed_requests: number;
  completed_quantity: number;
}

export interface RequestAnalytics {
  generated_at: string;
  from_at: string | null;
  to_at: string | null;
  total_requests: number;
  completed_requests: number;
  approval_duration: RequestDurationMetric;
  preparation_duration: RequestDurationMetric;
  transport_duration: RequestDurationMetric;
  acceptance_duration: RequestDurationMetric;
  on_time: RequestRateMetric;
  discrepancy: RequestRateMetric;
  shortage: RequestRateMetric;
  overage: RequestRateMetric;
  rejection_reasons: { reason: string; count: number }[];
  cancellation_reasons: { reason: string; count: number }[];
  throughput_by_warehouse: RequestThroughput[];
  throughput_by_mover: RequestThroughput[];
}

export interface RequestReportFilters {
  warehouse_id?: number;
  assigned_mover_user_id?: number;
  from_at?: string;
  to_at?: string;
  severity?: RequestIssueSeverity;
  issue_type?: string;
}

export interface RequestSuggestion {
  direction: RequestDirection;
  warehouse_id: number;
  analysis_as_of: string;
  current_available: number;
  min_inventory: number;
  max_capacity: number;
  current_occupied: number;
  baseline_gap: number;
  minimum_gap: number;
  pending_inbound: number;
  pending_backorder: number;
  scheduled_inbound: number;
  scheduled_return: number;
  history_window_30_start: string;
  history_window_90_start: string;
  history_window_end: string;
  history_30_quantity: number;
  history_90_quantity: number;
  history_30_daily_rate: number;
  history_90_daily_rate: number;
  history_30_weight: number;
  history_90_weight: number;
  normalized_30_weight: number;
  normalized_90_weight: number;
  sample_size: number;
  history_days: number;
  confidence: "insufficient" | "low" | "medium" | "high" | "not_applicable";
  weighted_daily_rate: number;
  daily_demand_forecast: number;
  lead_time_days: number;
  lead_time_demand: number;
  safety_stock_percent: number;
  safety_stock_quantity: number;
  forecast_adjustment: number | null;
  adjustment_quantity: number;
  target_inventory: number;
  capacity_limit: number;
  capacity_available: number;
  capacity_cap_applied: boolean;
  fallback_used: boolean;
  fallback_reason: string | null;
  suggested_quantity: number;
  eligible_return: number;
  consumption_definition: string;
  formula: string;
  explanation: string;
}

export interface ReturnSource {
  id: number;
  warehouse_id: number;
  completed_at: string;
  origin: RequestOrigin;
  delivered_quantity: number;
  eligible_quantity: number;
  pallets: ReturnPalletContext[];
  has_unassigned_boxes: boolean;
}

export interface ReturnPalletContext {
  pallet_id: number | null;
  pallet_number: string | null;
}

export interface ReturnCandidate {
  box_id: number;
  box_number: string;
  lot: string;
  lot_id: number;
  pallet_id: number | null;
  pallet_number: string | null;
  contents: string | null;
  file_count: number;
  file_summary: string | null;
  status: "ready_to_return";
}

export interface RequestFilters {
  warehouse_id?: number;
  direction?: RequestDirection;
  status?: RequestStatus;
  priority?: RequestPriority;
  origin?: RequestOrigin;
  assigned_mover_user_id?: number;
  queue?: RequestQueue;
  search?: string;
  sort_by?: RequestSortField;
  sort_dir?: "asc" | "desc";
}

export type PalletProgressState =
  | "active"
  | "in_progress"
  | "complete"
  | "no_eligible";
export type PalletSortField =
  | "pallet_number"
  | "completion"
  | "box_count"
  | "latest_activity";
export type PalletEventType =
  | "created"
  | "renumbered"
  | "archived"
  | "restored"
  | "moved"
  | "lot_reassigned"
  | "merged_absorbed"
  | "boxes_assigned"
  | "boxes_unassigned";

export interface PalletStatusCounts extends Record<BoxStatus, number> {
  quarantined: number;
  received: number;
  processing: number;
  incomplete: number;
  ready_to_return: number;
  returned: number;
}

export interface PalletSummary {
  id: number;
  lot_id: number;
  lot_name: string;
  warehouse_ids: number[];
  warehouse_names: string[];
  pallet_number: string;
  normalized_pallet_number: string;
  version: number;
  is_active: boolean;
  archived_at: string | null;
  archived_by_user_id: number | null;
  archive_reason: string | null;
  absorbed_into_pallet_id: number | null;
  absorbed_at: string | null;
  absorbed_by_user_id: number | null;
  created_at: string;
  updated_at: string;
  created_by_user_id: number | null;
  updated_by_user_id: number | null;
  physical_box_count: number;
  box_count: number;
  active_file_count: number;
  archived_file_count: number;
  status_counts: PalletStatusCounts;
  eligible_box_count: number;
  completed_box_count: number;
  completion_percent: number | null;
  progress_state: PalletProgressState;
  latest_activity: string;
}

export type PalletDetail = PalletSummary;

export interface PalletOption {
  id: number;
  pallet_number: string;
  normalized_pallet_number: string;
  lot_id: number;
  lot_name: string;
  warehouse_ids: number[];
  warehouse_names: string[];
  is_active: boolean;
  exact_normalized_match: boolean;
}

export interface PalletEvent {
  id: number;
  pallet_id: number;
  event_type: PalletEventType;
  old_pallet_number: string | null;
  new_pallet_number: string | null;
  from_warehouse_id: number | null;
  to_warehouse_id: number | null;
  actor_user_id: number | null;
  reason: string | null;
  occurred_at: string;
  metadata: Record<string, unknown>;
}

export interface PalletFilters {
  search?: string;
  lot_id?: number;
  progress_state?: PalletProgressState;
  include_inactive?: boolean;
  sort_by?: PalletSortField;
  sort_dir?: "asc" | "desc";
}

export interface PalletOptionFilters {
  search?: string;
  lot_id?: number;
  include_inactive?: boolean;
}

export interface PalletCreatePayload {
  lot_id: number;
  warehouse_id: number;
  pallet_number: string;
}

export interface PalletRenamePayload {
  new_pallet_number: string;
  reason: string;
  expected_version: number;
}

export interface PalletStateChangePayload {
  reason: string;
  expected_version: number;
}

export interface PalletBoxMutationPayload {
  box_ids: number[];
  reason: string;
}

export interface PalletBoxMutationResult {
  pallet_id: number;
  updated_box_ids: number[];
  skipped: Record<string, unknown>[];
  cancelled_request_ids: number[];
}

export interface PalletIntegrityGroup {
  count: number;
  box_ids: number[];
}

export interface PalletIntegrity {
  orphaned_pallet_ids: PalletIntegrityGroup;
  cross_lot: PalletIntegrityGroup;
  inactive_pallet_assignments: PalletIntegrityGroup;
  unassigned_active_boxes: PalletIntegrityGroup;
}

export interface PalletConflictCurrent {
  id: number;
  pallet_number: string;
  normalized_pallet_number: string;
  version: number;
  is_active: boolean;
  updated_at: string;
}

export interface PalletConflict {
  code: string;
  message: string;
  current: PalletConflictCurrent | null;
  box_count: number | null;
}

export interface PalletExportFilters extends PalletFilters {}

export type LotProgressState =
  | "active"
  | "in_progress"
  | "complete"
  | "no_eligible";
export type LotSortField =
  | "name"
  | "completion"
  | "box_count"
  | "last_activity";

export interface LotStatusCounts extends Record<BoxStatus, number> {
  quarantined: number;
  received: number;
  processing: number;
  incomplete: number;
  ready_to_return: number;
  returned: number;
}

export interface LotSummary {
  id: number;
  name: string;
  normalized_name: string;
  version: number;
  created_at: string;
  updated_at: string;
  physical_box_count: number;
  box_count: number;
  active_file_count: number;
  archived_file_count: number;
  status_counts: LotStatusCounts;
  eligible_box_count: number;
  completed_box_count: number;
  completion_percent: number | null;
  progress_state: LotProgressState;
  warehouse_count: number;
  warehouse_names: string[];
  staged_receipt_count: number;
  last_box_activity: string | null;
  acl_scoped: boolean;
  scope_label: "global" | "accessible_warehouses_only";
}

export interface LotEvent {
  id: number;
  lot_id: number;
  event_type: "created" | "renamed" | "reassigned" | "merged";
  old_name: string | null;
  new_name: string | null;
  actor_user_id: number | null;
  reason: string | null;
  occurred_at: string;
  metadata: Record<string, unknown>;
}

export interface LotDetail extends LotSummary {
  audit_history: LotEvent[];
  audit_history_included: boolean;
}

export interface LotIdentity {
  id: number;
  name: string;
  version: number;
}

export interface MergedLot {
  state: "merged";
  id: number;
  name: string;
  version: number;
  merged_at: string;
  merged_by_user_id: number | null;
  merged_into: LotIdentity;
}

export type LotMergeSide = "source" | "target";

export interface LotArchivedBoxCollision {
  box_number: string;
  source_box_id: number;
  source_box_archived: boolean;
  target_box_id: number;
  target_box_archived: boolean;
  survivor_box_id: number;
  survivor_lot_side: LotMergeSide;
  removed_box_id: number;
  removed_lot_side: LotMergeSide;
  request_item_relink_count: number;
  discrepancy_relink_count: number;
  box_event_delete_count: number;
  file_delete_count: number;
  file_event_delete_count: number;
}

export interface LotFileReferenceCollision {
  normalized_reference: string;
  source_file_ids: number[];
  target_file_ids: number[];
  active_file_ids: number[];
  archived_only: boolean;
  survivor_file_id: number | null;
  removed_file_ids: number[];
}

export interface LotHardBoxOverlap {
  box_number: string;
  source_box_ids: number[];
  target_box_ids: number[];
  source_active_box_ids: number[];
  target_active_box_ids: number[];
}

export interface LotMergeCandidate {
  source: LotIdentity;
  target: LotIdentity;
  merge_allowed: boolean;
  overlapping_box_numbers: string[];
  overlapping_box_count: number;
  overlap_list_truncated: boolean;
  resolvable_archived_collisions: LotArchivedBoxCollision[];
  resolvable_archived_collision_count: number;
  resolvable_archived_collisions_truncated: boolean;
  hard_overlaps: LotHardBoxOverlap[];
  hard_overlap_count: number;
  hard_overlaps_truncated: boolean;
  pallet_collisions: LotPalletCollision[];
  pallet_collision_count: number;
  pallet_collisions_truncated: boolean;
  pallet_actions: LotPalletMergeAction[];
  pallet_action_count: number;
  pallet_actions_truncated: boolean;
  file_reference_collisions: LotFileReferenceCollision[];
  file_reference_collision_count: number;
  file_reference_collisions_truncated: boolean;
  active_file_reference_collision_count: number;
  archived_file_reference_collision_count: number;
  merge_allowed_with_archived_overwrite: boolean;
  requires_explicit_overwrite: boolean;
  collision_signature: string;
}

export interface LotPalletCollision {
  normalized_pallet_number: string;
  source_pallet_id: number;
  source_pallet_number: string;
  source_is_active: boolean;
  target_pallet_id: number;
  target_pallet_number: string;
  target_is_active: boolean;
  reason: "inactive_target";
}

export interface LotPalletMergeAction {
  source_pallet_id: number;
  source_pallet_number: string;
  source_is_active: boolean;
  action: "combine" | "transfer";
  target_pallet_id: number | null;
  box_count: number;
}

interface LotMergePayloadBase {
  target_lot_id: number;
  reason: string;
  expected_source_version: number;
  expected_target_version: number;
}

export type LotMergePayload =
  | (LotMergePayloadBase & {
      overwrite_archived_collisions?: false;
      expected_collision_signature?: never;
    })
  | (LotMergePayloadBase & {
      overwrite_archived_collisions: true;
      expected_collision_signature: string;
    });

export interface LotMergeResult {
  source: LotIdentity;
  target: LotIdentity;
  moved_box_count: number;
  moved_request_item_count: number;
  overwritten_archived_box_count: number;
  relinked_request_item_count: number;
  relinked_discrepancy_count: number;
  deleted_box_event_count: number;
  combined_pallet_count: number;
  moved_pallet_count: number;
  absorbed_pallet_ids: number[];
  moved_pallet_ids: number[];
  moved_file_count: number;
  overwritten_archived_file_count: number;
  deleted_file_event_count: number;
}

export interface LotOption {
  id: number;
  name: string;
  normalized_name: string;
  exact_normalized_match: boolean;
}

export interface LotFilters {
  search?: string;
  warehouse_id?: number;
  progress_state?: LotProgressState;
  sort_by?: LotSortField;
  sort_dir?: "asc" | "desc";
}

export interface LotCreatePayload {
  name: string;
  warehouse_id: number;
}

export interface LotRenamePayload {
  new_name: string;
  reason: string;
  expected_version: number;
}

export type LotPurgeEntityType =
  | "lot"
  | "box"
  | "pallet"
  | "request"
  | "event";

export type LotPurgeBlockerCode =
  | "merged_tombstone"
  | "merge_target"
  | "active_boxes"
  | "no_archived_boxes"
  | "no_self_receipts"
  | "staged_requests"
  | "open_requests"
  | "requests_not_completed"
  | "unsupported_request_origin"
  | "unsupported_request_direction"
  | "mixed_lot_receipt"
  | "incomplete_receipt_provenance"
  | "request_family"
  | "foreign_request_reference"
  | "unlinked_archived_boxes"
  | "shared_object_key"
  | "workflow_history"
  | "box_reassignment_history"
  | "merge_history"
  | "box_workflow_history";

export interface LotPurgeEntity {
  entity_type: LotPurgeEntityType;
  entity_id: number;
}

export interface LotPurgeBlocker {
  code: LotPurgeBlockerCode;
  message: string;
  remediation: string;
  entities: LotPurgeEntity[];
  entity_count: number;
  entities_truncated: boolean;
}

export interface LotPurgeRequestPreview {
  request_id: number;
  origin: string;
  status: string;
  direction: string;
  item_count: number;
  lot_item_count: number;
}

export interface LotPurgePreview {
  lot_id: number;
  lot_name: string;
  lot_version: number;
  active_box_count: number;
  active_box_ids: number[];
  active_box_ids_truncated: boolean;
  archived_box_count: number;
  archived_box_ids: number[];
  archived_box_ids_truncated: boolean;
  active_pallet_count: number;
  active_pallet_ids: number[];
  active_pallet_ids_truncated: boolean;
  archived_pallet_count: number;
  archived_pallet_ids: number[];
  archived_pallet_ids_truncated: boolean;
  linked_request_count: number;
  linked_request_ids: number[];
  linked_request_ids_truncated: boolean;
  requests: LotPurgeRequestPreview[];
  requests_truncated: boolean;
  object_key_count: number;
  file_count: number;
  active_file_count: number;
  archived_file_count: number;
  file_event_count: number;
  linked_file_snapshot_count: number;
  graph_signature: string;
  eligible: boolean;
  blockers: LotPurgeBlocker[];
  confirmation_policy: "exact_case_sensitive_no_normalization";
}

export interface LotPurgePayload {
  confirmation_name: string;
  reason: string;
  expected_version: number;
  expected_graph_signature?: string | null;
}

export type LotPurgeCleanupStatus =
  | "pending"
  | "not_required"
  | "in_progress"
  | "completed"
  | "partial_failure"
  | "failed";

export interface LotPurgeObjectFailure {
  object_key: string;
  error: string;
}

export interface LotPurgeResult {
  purge_audit_id: number;
  deleted_lot: LotIdentity;
  archived_box_count: number;
  pallet_count: number;
  receipt_count: number;
  object_key_count: number;
  object_cleanup_status: LotPurgeCleanupStatus;
  object_cleanup_failures: LotPurgeObjectFailure[];
  file_count: number;
  file_event_count: number;
  detached_file_snapshot_count: number;
}

export interface LotPurgeCleanupResult {
  purge_audit_id: number;
  object_cleanup_status: LotPurgeCleanupStatus;
  object_cleanup_failures: LotPurgeObjectFailure[];
}

export interface LotPurgeConflict {
  code: string;
  message: string;
  current_preview: LotPurgePreview | null;
}

export type LotForcePurgeBlockerCode =
  | LotPurgeBlockerCode
  | "invalid_identity"
  | "graph_changed";

export interface LotForcePurgeBlocker {
  code: LotForcePurgeBlockerCode;
  message: string;
  entity_ids: number[];
  entity_count: number;
  entity_ids_truncated: boolean;
}

export interface LotForcePurgeItemPosition {
  item_id: number;
  before_position: number;
  after_position: number | null;
}

export interface LotForcePurgeRequestRewrite {
  request_id: number;
  adjustment_event_type: "force_purge_adjusted";
  origin: string;
  status: string;
  direction: string;
  before_item_count: number;
  after_item_count: number;
  before_quantity: number;
  after_quantity: number;
  before_actual_received_quantity: number | null;
  after_actual_received_quantity: number | null;
  before_variance_quantity: number | null;
  after_variance_quantity: number | null;
  item_positions: LotForcePurgeItemPosition[];
  item_positions_truncated: boolean;
  removed_item_ids: number[];
  removed_item_count: number;
  removed_item_ids_truncated: boolean;
  removed_discrepancy_ids: number[];
  removed_discrepancy_count: number;
  removed_discrepancy_ids_truncated: boolean;
  removed_discrepancy_photo_ids: number[];
  removed_discrepancy_photo_count: number;
  removed_discrepancy_photo_ids_truncated: boolean;
  preserved_sibling_lot_ids: number[];
  preserved_sibling_lot_count: number;
  preserved_sibling_lot_ids_truncated: boolean;
}

export interface LotForcePurgeLineageDetachment {
  request_id: number;
  field_name:
    | "source_inbound_request_id"
    | "parent_request_id"
    | "root_request_id";
  deleted_target_request_id: number;
}

export interface LotForcePurgeObjectCleanupPlan {
  deletable_key_count: number;
  shared_skipped_key_count: number;
}

export interface LotForcePurgePreview {
  lot_id: number;
  lot_name: string;
  lot_version: number;
  confirmation_phrase: string;
  active_box_ids: number[];
  active_box_count: number;
  active_box_ids_truncated: boolean;
  archived_box_ids: number[];
  archived_box_count: number;
  archived_box_ids_truncated: boolean;
  active_pallet_ids: number[];
  active_pallet_count: number;
  active_pallet_ids_truncated: boolean;
  archived_pallet_ids: number[];
  archived_pallet_count: number;
  archived_pallet_ids_truncated: boolean;
  touched_request_ids: number[];
  touched_request_count: number;
  touched_request_ids_truncated: boolean;
  request_rewrites: LotForcePurgeRequestRewrite[];
  request_rewrite_count: number;
  request_rewrites_truncated: boolean;
  fully_deleted_request_ids: number[];
  fully_deleted_request_count: number;
  fully_deleted_request_ids_truncated: boolean;
  incoming_lineage_detachments: LotForcePurgeLineageDetachment[];
  incoming_lineage_detachment_count: number;
  incoming_lineage_detachments_truncated: boolean;
  file_count: number;
  active_file_count: number;
  archived_file_count: number;
  file_event_count: number;
  linked_file_snapshot_count: number;
  object_cleanup: LotForcePurgeObjectCleanupPlan;
  hard_blockers: LotForcePurgeBlocker[];
  overridden_blockers: LotForcePurgeBlocker[];
  graph_signature: string;
  force_allowed: boolean;
  confirmation_policy: "exact_case_sensitive_no_normalization";
}

export interface LotForcePurgePayload {
  confirmation_name: string;
  confirmation_phrase: string;
  reason: string;
  expected_version: number;
  expected_graph_signature: string;
  acknowledged_blocker_codes: string[];
}

export interface LotForcePurgeResult {
  purge_audit_id: number;
  deleted_lot: LotIdentity;
  purge_mode: "force";
  active_box_count: number;
  archived_box_count: number;
  pallet_count: number;
  touched_request_count: number;
  rewritten_request_count: number;
  deleted_request_count: number;
  lineage_detachment_count: number;
  deletable_object_count: number;
  skipped_object_count: number;
  object_cleanup_status: LotPurgeCleanupStatus;
  object_cleanup_failure_count: number;
  file_count: number;
  file_event_count: number;
  detached_file_snapshot_count: number;
}

export interface LotForcePurgeConflict {
  code: string;
  message: string;
  current_preview: LotForcePurgePreview | null;
}

export interface BoxLotReassignmentPayload {
  lot_id: number;
  reason: string;
  expected_lot_version: number;
  pallet_id?: number;
  detach_pallet?: boolean;
}

export interface Notification {
  id: number;
  request_id: number | null;
  warehouse_id: number;
  kind: string;
  title: string;
  body: string;
  deep_link: string;
  read_at: string | null;
  created_at: string;
}

export interface NotificationPage {
  items: Notification[];
  total: number;
  unread: number;
  page: number;
  page_size: number;
}

export interface InboundRequestItemInput {
  lot: string;
  box_number: string;
  pallet_number?: string | null;
  pallet_id?: number | null;
  contents?: string;
  files?: FileInput[];
}

export type InboundCompletionClassification =
  | "create"
  | "relocate"
  | "blocked";

export type InboundTargetPalletResolution =
  | "existing"
  | "will_create"
  | "unassigned"
  | "preserve_existing"
  | "blocked";

export interface InboundCompletionPreviewRequest {
  inbound_items?: InboundRequestItemInput[] | null;
}

export interface InboundCompletionTargetPallet {
  resolution: InboundTargetPalletResolution;
  pallet_id: number | null;
  pallet_number: string | null;
}

export interface InboundCompletionSourceWarehouseCount {
  warehouse_id: number;
  warehouse_name: string;
  count: number;
}

export interface InboundCompletionPreviewRow {
  classification: InboundCompletionClassification;
  lot: string;
  box_number: string;
  normalized_lot: string;
  normalized_box_number: string;
  mapped_pallet_number: string | null;
  mapped_pallet_id: number | null;
  existing_box_id: number | null;
  current_status: BoxStatus | null;
  source_warehouse_id: number | null;
  source_warehouse_name: string | null;
  current_pallet_id: number | null;
  current_pallet_number: string | null;
  target_warehouse_id: number;
  target_warehouse_name: string;
  target_pallet_resolution: InboundCompletionTargetPallet;
  active_return_reservation_ids: number[];
  file_impacts: InboundFileImpact[];
  blocked_code: string | null;
  blocked_message: string | null;
}

export type InboundFileAction =
  | "create"
  | "update"
  | "move"
  | "preserve"
  | "blocked";

export interface InboundFileImpact extends FileInput {
  action: InboundFileAction;
  file_id: number | null;
  file_version: number | null;
  source_box_id: number | null;
  source_box_number: string | null;
  source_warehouse_id: number | null;
  source_warehouse_name: string | null;
  source_status: BoxStatus | null;
  target_box_id: number | null;
  blocked_code: string | null;
  blocked_message: string | null;
}

export interface InboundCompletionPreviewSummary {
  created: number;
  relocated: number;
  blocked: number;
  files_created: number;
  files_updated: number;
  files_moved: number;
  files_preserved: number;
  files_blocked: number;
  source_warehouse_counts: InboundCompletionSourceWarehouseCount[];
}

export interface InboundCompletionPreview {
  request_id: number;
  request_version: number;
  target_warehouse_id: number;
  target_warehouse_name: string;
  impact_signature: string;
  can_complete: boolean;
  summary: InboundCompletionPreviewSummary;
  rows: InboundCompletionPreviewRow[];
}

export interface InboundCompletionFields {
  accept_existing_received_boxes: boolean;
  accept_file_moves: boolean;
  inbound_impact_signature: string | null;
}

export interface ReviewedInboundCompletionFields
  extends InboundCompletionFields {
  inbound_impact_signature: string;
}

export interface RequestConflict {
  message: string;
  request_id: number;
  latest_version: number;
  latest_status: RequestStatus;
  relevant_events: RequestEvent[];
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

export type XlsxMappingUseCase = "box_import" | "inbound_acceptance";
export type XlsxLotSource = "fixed" | "column";

export interface XlsxColumnRef {
  index: number;
  header: string;
}

export interface XlsxColumnMappings {
  box_number: XlsxColumnRef;
  pallet_number?: XlsxColumnRef | null;
  lot?: XlsxColumnRef;
  file_reference?: XlsxColumnRef;
  file_description?: XlsxColumnRef;
  barcode?: XlsxColumnRef;
  contents?: XlsxColumnRef;
}

export interface XlsxMappingTemplate {
  id: number;
  owner_user_id: number;
  owner_name: string;
  warehouse_id: number | null;
  use_case: XlsxMappingUseCase;
  name: string;
  sheet_pattern: string;
  filename_fingerprint: string;
  header_fingerprint: string;
  column_mappings: XlsxColumnMappings;
  lot_source: XlsxLotSource;
  fixed_lot: string | null;
  row_start: number;
  include_rows_by_default: boolean;
  usage_count: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
  is_owner: boolean;
  is_shared: boolean;
  is_legacy_incomplete: boolean;
}

export interface XlsxMappingTemplateInput {
  use_case: XlsxMappingUseCase;
  name: string;
  warehouse_id: number | null;
  sheet_pattern: string;
  filename: string;
  headers: string[];
  column_mappings: XlsxColumnMappings;
  lot_source: XlsxLotSource;
  fixed_lot?: string | null;
  row_start: number;
  include_rows_by_default: boolean;
}

export type XlsxMappingTemplateUpdate = Partial<
  Omit<XlsxMappingTemplateInput, "use_case">
>;

export interface XlsxMappingSuggestion {
  template: XlsxMappingTemplate;
  confidence: number;
  explanation: string;
  exact_header_match: boolean;
}
