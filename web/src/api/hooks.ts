import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, buildQueryString } from "@/api/client";
import { stripGeneratedBarcodeFields } from "@/lib/barcode";
import type {
  Alert,
  AlertDetail,
  AlertRecipients,
  AlertTestEmailResult,
  BarcodeResolution,
  Box,
  BoxDeleteResult,
  BoxUpdateResult,
  BoxRequest,
  BoxEvent,
  BoxFilters,
  BulkBoxUpdate,
  BulkDeleteResult,
  BulkResult,
  CreateBoxRequestInput,
  DashboardSummary,
  Employee,
  EmployeeAverages,
  EmployeeImportItem,
  EmployeeImportResult,
  FileCreatePayload,
  FileIntegrity,
  FileInput,
  FileMovePayload,
  FileStateChangePayload,
  FileUpdatePayload,
  TrackedFile,
  TrackedFileEvent,
  TrackedFileFilters,
  ImportResult,
  InboundCompletionPreview,
  InboundCompletionPreviewRequest,
  InboundRequestItemInput,
  LotCreatePayload,
  LotDetail,
  LotEvent,
  LotFilters,
  LotForcePurgePayload,
  LotForcePurgePreview,
  LotForcePurgeResult,
  LotMergePayload,
  LotMergeResult,
  LotOption,
  LotPurgeCleanupResult,
  LotPurgePayload,
  LotPurgePreview,
  LotPurgeResult,
  LotRenamePayload,
  LotSummary,
  MergedLot,
  NotificationPage,
  Page,
  PalletBoxMutationPayload,
  PalletBoxMutationResult,
  PalletCreatePayload,
  PalletDetail,
  PalletEvent,
  PalletFilters,
  PalletIntegrity,
  PalletOption,
  PalletOptionFilters,
  PalletRenamePayload,
  PalletStateChangePayload,
  PalletSummary,
  ProductivityEntry,
  ProductivitySummary,
  RequestDirection,
  RequestAssignee,
  RequestAttachment,
  RequestComment,
  RequestDiscrepancy,
  RequestDiscrepancyInput,
  RequestDiscrepancyPhoto,
  RequestDocument,
  RequestDocumentType,
  RequestEvent,
  RequestFilters,
  RequestAnalytics,
  RequestReconciliation,
  RequestReportFilters,
  RequestSuggestion,
  ReturnCandidate,
  ReturnSource,
  StagedReceiptResult,
  BoxLotReassignmentPayload,
  User,
  Warehouse,
  XlsxMappingSuggestion,
  XlsxMappingTemplate,
  XlsxMappingTemplateInput,
  XlsxMappingTemplateUpdate,
  XlsxMappingUseCase,
  XlsxPreview,
} from "@/api/types";

export const queryKeys = {
  me: ["me"] as const,
  dashboard: ["dashboard"] as const,
  warehouses: (includeInactive = false) =>
    ["warehouses", includeInactive] as const,
  users: ["users"] as const,
  notifications: (page: number, pageSize: number) =>
    ["notifications", page, pageSize] as const,
  alerts: (open: boolean) => ["alerts", open] as const,
  alert: (id: number) => ["alert", id] as const,
  alertRecipients: ["alert-recipients"] as const,
  barcode: (barcode: string) => ["barcode", barcode] as const,
  box: (id: number) => ["box", id] as const,
  boxEvents: (id: number) => ["box-events", id] as const,
  boxes: (filters: BoxFilters, page: number, pageSize: number) =>
    ["boxes", filters, page, pageSize] as const,
  files: (filters: TrackedFileFilters, page: number, pageSize: number) =>
    ["files", filters, page, pageSize] as const,
  file: (id: number, includeInactive = false, includeArchived = false) =>
    ["file", id, includeInactive, includeArchived] as const,
  fileEvents: (id: number) => ["file-events", id] as const,
  fileIntegrity: ["file-integrity"] as const,
  lots: (filters: LotFilters, page: number, pageSize: number) =>
    ["lots", filters, page, pageSize] as const,
  lotOptions: (search: string, page: number, limit: number) =>
    ["lot-options", search, page, limit] as const,
  lot: (id: number) => ["lot", id] as const,
  lotEvents: (id: number) => ["lot-events", id] as const,
  lotPurgePreview: (id: number) => ["lot-purge-preview", id] as const,
  lotForcePurgePreview: (id: number) =>
    ["lot-force-purge-preview", id] as const,
  lotBoxes: (id: number, filters: BoxFilters, page: number, pageSize: number) =>
    ["lot-boxes", id, filters, page, pageSize] as const,
  pallets: (filters: PalletFilters, page: number, pageSize: number) =>
    ["pallets", filters, page, pageSize] as const,
  palletOptions: (filters: PalletOptionFilters, page: number, limit: number) =>
    ["pallet-options", filters, page, limit] as const,
  pallet: (id: number, includeInactive = false) =>
    ["pallet", id, includeInactive] as const,
  palletEvents: (id: number, includeInactive = false) =>
    ["pallet-events", id, includeInactive] as const,
  palletIntegrity: ["pallet-integrity"] as const,
  requests: (filters: RequestFilters, page: number, pageSize: number) =>
    ["requests", filters, page, pageSize] as const,
  request: (id: number) => ["request", id] as const,
  requestEvents: (id: number) => ["request-events", id] as const,
  requestDocuments: (id: number) => ["request-documents", id] as const,
  requestComments: (id: number) => ["request-comments", id] as const,
  requestAttachments: (id: number) => ["request-attachments", id] as const,
  requestAssignees: (warehouseId: number | undefined) =>
    ["request-assignees", warehouseId ?? null] as const,
  requestReconciliation: (
    filters: RequestReportFilters,
    page: number,
    pageSize: number,
  ) => ["request-reconciliation", filters, page, pageSize] as const,
  requestAnalytics: (filters: RequestReportFilters) =>
    ["request-analytics", filters] as const,
  requestSuggestion: (
    warehouseId: number | undefined,
    direction: RequestDirection,
  ) => ["request-suggestion", warehouseId ?? null, direction] as const,
  returnSources: (warehouseId: number | undefined) =>
    ["return-sources", warehouseId ?? null] as const,
  returnCandidates: (sourceInboundRequestId: number | undefined) =>
    ["return-candidates", sourceInboundRequestId ?? null] as const,
  xlsxMappingTemplates: (
    useCase: XlsxMappingUseCase,
    warehouseId: number | undefined,
  ) => ["xlsx-mapping-templates", useCase, warehouseId ?? null] as const,
  employees: (
    warehouseId?: number,
    includeInactive?: boolean,
    page?: number,
    pageSize?: number,
  ) =>
    [
      "employees",
      warehouseId ?? null,
      !!includeInactive,
      page ?? 1,
      pageSize ?? 500,
    ] as const,
  productivityEntries: (filters: ProductivityEntryFilters) =>
    ["productivity-entries", filters] as const,
  productivityDaily: (warehouseId?: number, date?: string) =>
    ["productivity-daily", warehouseId ?? null, date ?? null] as const,
  productivityWeekly: (warehouseId?: number, weekStart?: string) =>
    ["productivity-weekly", warehouseId ?? null, weekStart ?? null] as const,
  productivityMonthly: (warehouseId?: number, month?: string) =>
    ["productivity-monthly", warehouseId ?? null, month ?? null] as const,
  productivityThreeMonth: (warehouseId?: number, anchorDate?: string) =>
    ["productivity-three-month", warehouseId ?? null, anchorDate ?? null] as const,
  employeeAverages: (warehouseId: number, anchorDate?: string) =>
    ["employee-averages", warehouseId, anchorDate ?? null] as const,
};

export interface ProductivityEntryFilters {
  warehouse_id?: number;
  employee_id?: number;
  from_date?: string;
  to_date?: string;
  [key: string]: unknown;
}

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: async () => (await api.get<User>("/auth/me")).data,
  });
}

export function useDashboard() {
  return useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: async () =>
      (await api.get<DashboardSummary>("/dashboard/summary")).data,
    refetchInterval: 30_000,
  });
}

export function useWarehouses(includeInactive = false) {
  return useQuery({
    queryKey: queryKeys.warehouses(includeInactive),
    queryFn: async () =>
      (
        await api.get<Warehouse[]>(
          `/warehouses${buildQueryString({
            include_inactive: includeInactive,
          })}`,
        )
      ).data,
  });
}

function invalidateWarehouseState(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["warehouses"] });
  qc.invalidateQueries({ queryKey: queryKeys.dashboard });
  qc.invalidateQueries({ queryKey: queryKeys.users });
  qc.invalidateQueries({ queryKey: queryKeys.alertRecipients });
  qc.invalidateQueries({ queryKey: ["productivity-daily"] });
  qc.invalidateQueries({ queryKey: ["productivity-weekly"] });
  qc.invalidateQueries({ queryKey: ["productivity-monthly"] });
  qc.invalidateQueries({ queryKey: ["productivity-three-month"] });
  qc.invalidateQueries({ queryKey: ["employee-averages"] });
}

export function useUpdateWarehouse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      patch: Partial<
        Pick<
          Warehouse,
          | "name"
          | "min_inventory"
          | "max_capacity"
          | "min_pages_per_day"
          | "lead_time_days"
          | "safety_stock_percent"
          | "history_30_weight"
          | "history_90_weight"
          | "forecast_adjustment"
          | "receipt_mode"
          | "require_erp_document"
          | "quarantine_imports"
          | "quarantine_manual_receipts"
          | "two_person_approval_threshold"
        >
      >;
    }) => (await api.patch<Warehouse>(`/warehouses/${input.id}`, input.patch)).data,
    onSuccess: () => {
      invalidateWarehouseState(qc);
    },
  });
}

export function useCreateWarehouse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      name: string;
      min_inventory?: number;
      max_capacity?: number;
      min_pages_per_day?: number | null;
      lead_time_days?: number;
      safety_stock_percent?: number;
      history_30_weight?: number;
      history_90_weight?: number;
      forecast_adjustment?: number | null;
      receipt_mode?: Warehouse["receipt_mode"];
      require_erp_document?: boolean;
      quarantine_imports?: boolean;
      quarantine_manual_receipts?: boolean;
      two_person_approval_threshold?: number | null;
    }) => (await api.post<Warehouse>("/warehouses", input)).data,
    onSuccess: () => {
      invalidateWarehouseState(qc);
    },
  });
}

export function useArchiveWarehouse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (warehouseId: number) =>
      (await api.delete<Warehouse>(`/warehouses/${warehouseId}`)).data,
    onSuccess: () => invalidateWarehouseState(qc),
  });
}

export function useRestoreWarehouse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (warehouseId: number) =>
      (await api.post<Warehouse>(`/warehouses/${warehouseId}/restore`)).data,
    onSuccess: () => invalidateWarehouseState(qc),
  });
}

export function useUsers() {
  return useQuery({
    queryKey: queryKeys.users,
    queryFn: async () => (await api.get<User[]>("/users")).data,
  });
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      patch: {
        role?: User["role"];
        is_active?: boolean;
        role_override?: boolean;
        email_alerts_enabled?: boolean;
        email_requests_enabled?: boolean;
        warehouse_ids?: number[];
      };
    }) => (await api.patch<User>(`/users/${input.id}`, input.patch)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.users });
      // ACL or opt-out changes shift the alert recipient preview as well.
      qc.invalidateQueries({ queryKey: queryKeys.alertRecipients });
      // ACL changes can flip what the affected user (or the admin themselves
      // when adjusting their own row) sees; refresh warehouse-scoped views.
      qc.invalidateQueries({ queryKey: ["warehouses"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
  });
}

export function useUpdateMyRequestEmailPreference() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean) =>
      (
        await api.patch<User>("/users/me/preferences", {
          email_requests_enabled: enabled,
        })
      ).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.me });
      qc.invalidateQueries({ queryKey: queryKeys.users });
    },
  });
}

export function useNotifications(page = 1, pageSize = 25) {
  return useQuery({
    queryKey: queryKeys.notifications(page, pageSize),
    queryFn: async () =>
      (
        await api.get<NotificationPage>(
          `/notifications${buildQueryString({
            page,
            page_size: pageSize,
          })}`,
        )
      ).data,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
}

export function useMarkNotificationsRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (notificationIds?: number[]) =>
      (
        await api.post<{ unread: number }>("/notifications/mark-read", {
          notification_ids: notificationIds,
        })
      ).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

export function useAlerts(onlyOpen = true) {
  return useQuery({
    queryKey: queryKeys.alerts(onlyOpen),
    queryFn: async () =>
      (await api.get<Alert[]>(`/alerts${buildQueryString({ only_open: onlyOpen })}`)).data,
    refetchInterval: 30_000,
  });
}

export function useAcknowledgeAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.post<Alert>(`/alerts/${id}/ack`)).data,
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: queryKeys.alert(id) });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
  });
}

export function useAlert(id: number | undefined) {
  return useQuery({
    queryKey: id ? queryKeys.alert(id) : ["alert", "noop"],
    queryFn: async () =>
      (await api.get<AlertDetail>(`/alerts/${id}`)).data,
    enabled: !!id,
    refetchInterval: 30_000,
  });
}

export function useAlertRecipients() {
  return useQuery({
    queryKey: queryKeys.alertRecipients,
    queryFn: async () =>
      (await api.get<AlertRecipients>("/alerts/recipients")).data,
  });
}

export function useSendTestAlertEmail() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.post<AlertTestEmailResult>(`/alerts/${id}/test-email`)).data,
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: queryKeys.alert(id) });
    },
  });
}

export function useBoxes(filters: BoxFilters, page: number, pageSize: number) {
  return useQuery({
    queryKey: queryKeys.boxes(filters, page, pageSize),
    queryFn: async () =>
      (
        await api.get<Page<Box>>(
          `/boxes${buildQueryString({ ...filters, page, page_size: pageSize })}`,
        )
      ).data,
    placeholderData: (prev) => prev,
  });
}

export function useBarcode(barcode: string | undefined) {
  return useQuery({
    queryKey: barcode ? queryKeys.barcode(barcode) : ["barcode", "noop"],
    queryFn: async () =>
      (
        await api.get<BarcodeResolution>(
          `/barcodes/${encodeURIComponent(barcode as string)}`,
        )
      ).data,
    enabled: !!barcode,
  });
}

export function useBox(id: number | undefined) {
  return useQuery({
    queryKey: id ? queryKeys.box(id) : ["box", "noop"],
    queryFn: async () => (await api.get<Box>(`/boxes/${id}`)).data,
    enabled: !!id,
  });
}

export function useBoxEvents(id: number | undefined) {
  return useQuery({
    queryKey: id ? queryKeys.boxEvents(id) : ["box-events", "noop"],
    queryFn: async () => (await api.get<BoxEvent[]>(`/boxes/${id}/events`)).data,
    enabled: !!id,
  });
}

export function useFiles(
  filters: TrackedFileFilters,
  page: number,
  pageSize: number,
) {
  return useQuery({
    queryKey: queryKeys.files(filters, page, pageSize),
    queryFn: async () =>
      (
        await api.get<Page<TrackedFile>>(
          `/files${buildQueryString({ ...filters, page, page_size: pageSize })}`,
        )
      ).data,
    placeholderData: (previous) => previous,
  });
}

export function useFile(
  id: number | undefined,
  includeInactive = false,
  includeArchived = false,
) {
  return useQuery({
    queryKey: id
      ? queryKeys.file(id, includeInactive, includeArchived)
      : ["file", "noop"],
    queryFn: async () =>
      (
        await api.get<TrackedFile>(
          `/files/${id}${buildQueryString({
            include_inactive: includeInactive || undefined,
            include_archived: includeArchived || undefined,
          })}`,
        )
      ).data,
    enabled: !!id,
  });
}

export function useFileEvents(id: number | undefined) {
  return useQuery({
    queryKey: id ? queryKeys.fileEvents(id) : ["file-events", "noop"],
    queryFn: async () =>
      (await api.get<TrackedFileEvent[]>(`/files/${id}/events`)).data,
    enabled: !!id,
  });
}

export function useFileIntegrity(enabled = true) {
  return useQuery({
    queryKey: queryKeys.fileIntegrity,
    queryFn: async () =>
      (await api.get<FileIntegrity>("/files/integrity")).data,
    enabled,
  });
}

function invalidateFileState(
  qc: ReturnType<typeof useQueryClient>,
  fileId?: number,
) {
  qc.invalidateQueries({ queryKey: ["files"] });
  qc.invalidateQueries({ queryKey: ["file-integrity"] });
  if (fileId !== undefined) {
    qc.invalidateQueries({ queryKey: ["file", fileId] });
    qc.invalidateQueries({ queryKey: queryKeys.fileEvents(fileId) });
  } else {
    qc.invalidateQueries({ queryKey: ["file"] });
    qc.invalidateQueries({ queryKey: ["file-events"] });
  }
  qc.invalidateQueries({ queryKey: ["boxes"] });
  qc.invalidateQueries({ queryKey: ["box"] });
  qc.invalidateQueries({ queryKey: ["lots"] });
  qc.invalidateQueries({ queryKey: ["lot"] });
  qc.invalidateQueries({ queryKey: ["lot-boxes"] });
  qc.invalidateQueries({ queryKey: ["pallets"] });
  qc.invalidateQueries({ queryKey: ["pallet"] });
  qc.invalidateQueries({ queryKey: ["requests"] });
  qc.invalidateQueries({ queryKey: ["request"] });
  qc.invalidateQueries({ queryKey: ["request-reconciliation"] });
  qc.invalidateQueries({ queryKey: ["request-suggestion"] });
  qc.invalidateQueries({ queryKey: ["return-sources"] });
  qc.invalidateQueries({ queryKey: ["return-candidates"] });
  qc.invalidateQueries({ queryKey: queryKeys.dashboard });
}

export function useCreateFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: FileCreatePayload) =>
      (await api.post<TrackedFile>("/files", stripGeneratedBarcodeFields(payload))).data,
    onSettled: (file) => invalidateFileState(qc, file?.id),
  });
}

export function useUpdateFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { id: number; payload: FileUpdatePayload }) =>
      (
        await api.patch<TrackedFile>(
          `/files/${input.id}`,
          stripGeneratedBarcodeFields(input.payload),
        )
      ).data,
    onSettled: (_file, _error, input) => invalidateFileState(qc, input.id),
  });
}

export function useMoveFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { id: number; payload: FileMovePayload }) =>
      (
        await api.post<TrackedFile>(
          `/files/${input.id}/move`,
          stripGeneratedBarcodeFields(input.payload),
        )
      ).data,
    onSettled: (_file, _error, input) => invalidateFileState(qc, input.id),
  });
}

function useFileStateMutation(action: "archive" | "restore") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      payload: FileStateChangePayload;
    }) =>
      (
        await api.post<TrackedFile>(
          `/files/${input.id}/${action}`,
          stripGeneratedBarcodeFields(input.payload),
        )
      ).data,
    onSettled: (_file, _error, input) => invalidateFileState(qc, input.id),
  });
}

export function useArchiveFile() {
  return useFileStateMutation("archive");
}

export function useRestoreFile() {
  return useFileStateMutation("restore");
}

function invalidateLotState(
  qc: ReturnType<typeof useQueryClient>,
  lotIds: number[] = [],
) {
  qc.invalidateQueries({ queryKey: ["lots"] });
  qc.invalidateQueries({ queryKey: ["lot-options"] });
  qc.invalidateQueries({ queryKey: ["files"] });
  if (lotIds.length === 0) {
    qc.invalidateQueries({ queryKey: ["lot"] });
    qc.invalidateQueries({ queryKey: ["lot-events"] });
    qc.invalidateQueries({ queryKey: ["lot-boxes"] });
    qc.invalidateQueries({ queryKey: ["lot-purge-preview"] });
    qc.invalidateQueries({ queryKey: ["lot-force-purge-preview"] });
  }
  for (const id of lotIds) {
    qc.invalidateQueries({ queryKey: queryKeys.lot(id) });
    qc.invalidateQueries({ queryKey: queryKeys.lotEvents(id) });
    qc.invalidateQueries({ queryKey: queryKeys.lotPurgePreview(id) });
    qc.invalidateQueries({ queryKey: queryKeys.lotForcePurgePreview(id) });
    qc.invalidateQueries({ queryKey: ["lot-boxes", id] });
  }
}

export function useLots(filters: LotFilters, page: number, pageSize: number) {
  return useQuery({
    queryKey: queryKeys.lots(filters, page, pageSize),
    queryFn: async () =>
      (
        await api.get<Page<LotSummary>>(
          `/lots${buildQueryString({ ...filters, page, page_size: pageSize })}`,
        )
      ).data,
    placeholderData: (previous) => previous,
  });
}

export function useLotOptions(search: string, page = 1, limit = 25) {
  return useQuery({
    queryKey: queryKeys.lotOptions(search, page, limit),
    queryFn: async () =>
      (
        await api.get<Page<LotOption>>(
          `/lots/options${buildQueryString({ search, page, limit })}`,
        )
      ).data,
  });
}

export function useLot(id: number | undefined) {
  return useQuery({
    queryKey: id ? queryKeys.lot(id) : ["lot", "noop"],
    queryFn: async () =>
      (await api.get<LotDetail | MergedLot>(`/lots/${id}`)).data,
    enabled: !!id,
  });
}

export function useLotEvents(id: number | undefined) {
  return useQuery({
    queryKey: id ? queryKeys.lotEvents(id) : ["lot-events", "noop"],
    queryFn: async () =>
      (await api.get<LotDetail>(`/lots/${id}`)).data.audit_history as LotEvent[],
    enabled: !!id,
  });
}

export function useLotPurgePreview(
  id: number | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: id ? queryKeys.lotPurgePreview(id) : ["lot-purge-preview", "noop"],
    queryFn: async () =>
      (await api.get<LotPurgePreview>(`/lots/${id}/purge-preview`)).data,
    enabled: enabled && !!id,
    staleTime: 0,
  });
}

export function useLotForcePurgePreview(
  id: number | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: id
      ? queryKeys.lotForcePurgePreview(id)
      : ["lot-force-purge-preview", "noop"],
    queryFn: async () =>
      (
        await api.get<LotForcePurgePreview>(
          `/lots/${id}/force-purge-preview`,
        )
      ).data,
    enabled: enabled && !!id,
    staleTime: 0,
  });
}

export function useLotBoxes(
  id: number | undefined,
  filters: BoxFilters,
  page: number,
  pageSize: number,
) {
  return useQuery({
    queryKey: id
      ? queryKeys.lotBoxes(id, filters, page, pageSize)
      : ["lot-boxes", "noop"],
    queryFn: async () =>
      (
        await api.get<Page<Box>>(
          `/lots/${id}/boxes${buildQueryString({
            ...filters,
            page,
            page_size: pageSize,
          })}`,
        )
      ).data,
    enabled: !!id,
    placeholderData: (previous) => previous,
  });
}

function invalidatePalletState(
  qc: ReturnType<typeof useQueryClient>,
  palletIds: number[] = [],
) {
  qc.invalidateQueries({ queryKey: ["pallets"] });
  qc.invalidateQueries({ queryKey: ["pallet-options"] });
  qc.invalidateQueries({ queryKey: ["pallet-integrity"] });
  if (palletIds.length === 0) {
    qc.invalidateQueries({ queryKey: ["pallet"] });
    qc.invalidateQueries({ queryKey: ["pallet-events"] });
  } else {
    for (const id of new Set(palletIds)) {
      qc.invalidateQueries({ queryKey: ["pallet", id] });
      qc.invalidateQueries({ queryKey: ["pallet-events", id] });
    }
  }
  invalidateLotState(qc);
  qc.invalidateQueries({ queryKey: ["boxes"] });
  qc.invalidateQueries({ queryKey: ["box"] });
  qc.invalidateQueries({ queryKey: ["requests"] });
  qc.invalidateQueries({ queryKey: ["request"] });
  qc.invalidateQueries({ queryKey: ["return-sources"] });
  qc.invalidateQueries({ queryKey: ["return-candidates"] });
  qc.invalidateQueries({ queryKey: queryKeys.dashboard });
}

export function usePallets(
  filters: PalletFilters,
  page: number,
  pageSize: number,
) {
  return useQuery({
    queryKey: queryKeys.pallets(filters, page, pageSize),
    queryFn: async () =>
      (
        await api.get<Page<PalletSummary>>(
          `/pallets${buildQueryString({ ...filters, page, page_size: pageSize })}`,
        )
      ).data,
    placeholderData: (previous) => previous,
  });
}

export function usePalletOptions(
  filters: PalletOptionFilters,
  page = 1,
  limit = 25,
) {
  return useQuery({
    queryKey: queryKeys.palletOptions(filters, page, limit),
    queryFn: async () =>
      (
        await api.get<Page<PalletOption>>(
          `/pallets/options${buildQueryString({ ...filters, page, limit })}`,
        )
      ).data,
    enabled: filters.lot_id !== undefined,
  });
}

export function usePallet(id: number | undefined, includeInactive = false) {
  return useQuery({
    queryKey: id
      ? queryKeys.pallet(id, includeInactive)
      : ["pallet", "noop"],
    queryFn: async () =>
      (
        await api.get<PalletDetail>(
          `/pallets/${id}${buildQueryString({
            include_inactive: includeInactive,
          })}`,
        )
      ).data,
    enabled: !!id,
  });
}

export function usePalletEvents(
  id: number | undefined,
  includeInactive = false,
) {
  return useQuery({
    queryKey: id
      ? queryKeys.palletEvents(id, includeInactive)
      : ["pallet-events", "noop"],
    queryFn: async () =>
      (
        await api.get<PalletEvent[]>(
          `/pallets/${id}/events${buildQueryString({
            include_inactive: includeInactive,
          })}`,
        )
      ).data,
    enabled: !!id,
  });
}

export function usePalletIntegrity(enabled = true) {
  return useQuery({
    queryKey: queryKeys.palletIntegrity,
    queryFn: async () =>
      (await api.get<PalletIntegrity>("/pallets/integrity")).data,
    enabled,
  });
}

export function useCreatePallet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PalletCreatePayload) =>
      (await api.post<PalletSummary>("/pallets", payload)).data,
    onSuccess: (pallet) => invalidatePalletState(qc, [pallet.id]),
  });
}

export function useRenamePallet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      payload: PalletRenamePayload;
    }) =>
      (
        await api.patch<PalletSummary>(
          `/pallets/${input.id}/rename`,
          input.payload,
        )
      ).data,
    onSettled: (_data, _error, input) =>
      invalidatePalletState(qc, [input.id]),
  });
}

function usePalletStateMutation(action: "archive" | "restore") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      payload: PalletStateChangePayload;
    }) =>
      (
        await api.post<PalletSummary>(
          `/pallets/${input.id}/${action}`,
          input.payload,
        )
      ).data,
    onSettled: (_data, _error, input) =>
      invalidatePalletState(qc, [input.id]),
  });
}

export function useArchivePallet() {
  return usePalletStateMutation("archive");
}

export function useRestorePallet() {
  return usePalletStateMutation("restore");
}

function usePalletBoxMutation(action: "assign" | "detach") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      payload: PalletBoxMutationPayload;
    }) =>
      (
        await api.post<PalletBoxMutationResult>(
          `/pallets/${input.id}/boxes/${action}`,
          input.payload,
        )
      ).data,
    onSettled: (_data, _error, input) =>
      invalidatePalletState(qc, [input.id]),
  });
}

export function useAssignPalletBoxes() {
  return usePalletBoxMutation("assign");
}

export function useDetachPalletBoxes() {
  return usePalletBoxMutation("detach");
}

export function useCreateLot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: LotCreatePayload) =>
      (await api.post<LotSummary>("/lots", input)).data,
    onSuccess: (lot) => invalidateLotState(qc, [lot.id]),
  });
}

export function useRenameLot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { id: number; payload: LotRenamePayload }) =>
      (await api.patch<LotSummary>(`/lots/${input.id}/rename`, input.payload))
        .data,
    onSuccess: (lot) => {
      invalidateLotState(qc, [lot.id]);
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
    },
    onError: (_error, input) => invalidateLotState(qc, [input.id]),
  });
}

export function useMergeLots() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      sourceId: number;
      payload: LotMergePayload;
    }) =>
      (
        await api.post<LotMergeResult>(
          `/lots/${input.sourceId}/merge`,
          input.payload,
        )
      ).data,
    onSettled: (result, _error, input) => {
      invalidateLotState(qc, [
        input.sourceId,
        input.payload.target_lot_id,
        ...(result ? [result.source.id, result.target.id] : []),
      ]);
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: ["box"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["request"] });
      qc.invalidateQueries({ queryKey: ["request-suggestion"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
      qc.invalidateQueries({ queryKey: ["return-candidates"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
  });
}

function invalidatePurgedLotState(
  qc: ReturnType<typeof useQueryClient>,
  lotId: number,
) {
  qc.invalidateQueries({ queryKey: ["lots"] });
  qc.invalidateQueries({ queryKey: ["lot-options"] });
  qc.invalidateQueries({ queryKey: ["files"] });
  qc.invalidateQueries({ queryKey: ["boxes"] });
  qc.invalidateQueries({ queryKey: ["requests"] });
  qc.invalidateQueries({ queryKey: ["request-reconciliation"] });
  qc.invalidateQueries({ queryKey: ["request-analytics"] });
  qc.invalidateQueries({ queryKey: ["request-suggestion"] });
  qc.invalidateQueries({ queryKey: ["return-sources"] });
  qc.invalidateQueries({ queryKey: ["return-candidates"] });
  qc.invalidateQueries({ queryKey: ["notifications"] });
  qc.invalidateQueries({ queryKey: ["alerts"] });
  qc.invalidateQueries({ queryKey: ["exports"] });
  qc.invalidateQueries({ queryKey: queryKeys.dashboard });
  qc.removeQueries({ queryKey: ["box"] });
  qc.removeQueries({ queryKey: ["box-events"] });
  qc.removeQueries({ queryKey: ["request"] });
  qc.removeQueries({ queryKey: ["request-events"] });
  qc.removeQueries({ queryKey: ["request-documents"] });
  qc.removeQueries({ queryKey: ["request-comments"] });
  qc.removeQueries({ queryKey: ["request-attachments"] });
  qc.removeQueries({ queryKey: ["request-discrepancies"] });
  qc.removeQueries({ queryKey: queryKeys.lot(lotId), exact: true });
  qc.removeQueries({ queryKey: queryKeys.lotEvents(lotId), exact: true });
  qc.removeQueries({ queryKey: ["lot-boxes", lotId] });
  qc.removeQueries({ queryKey: queryKeys.lotPurgePreview(lotId), exact: true });
  qc.removeQueries({
    queryKey: queryKeys.lotForcePurgePreview(lotId),
    exact: true,
  });
}

export function usePurgeLot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { lotId: number; payload: LotPurgePayload }) =>
      (await api.post<LotPurgeResult>(`/lots/${input.lotId}/purge`, input.payload))
        .data,
    onSuccess: (_result, input) =>
      invalidatePurgedLotState(qc, input.lotId),
    onError: (_error, input) => {
      qc.invalidateQueries({
        queryKey: queryKeys.lotPurgePreview(input.lotId),
      });
    },
  });
}

export function useForcePurgeLot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      lotId: number;
      payload: LotForcePurgePayload;
    }) =>
      (
        await api.post<LotForcePurgeResult>(
          `/lots/${input.lotId}/force-purge`,
          input.payload,
        )
      ).data,
    onSuccess: (_result, input) =>
      invalidatePurgedLotState(qc, input.lotId),
    onError: (_error, input) => {
      qc.invalidateQueries({
        queryKey: queryKeys.lotForcePurgePreview(input.lotId),
      });
    },
  });
}

export function useRetryLotPurgeCleanup() {
  return useMutation({
    mutationFn: async (auditId: number) =>
      (
        await api.post<LotPurgeCleanupResult>(
          `/lots/purge-audits/${auditId}/cleanup-retry`,
        )
      ).data,
  });
}

export function useReassignBoxLot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      boxId: number;
      sourceLotId: number;
      payload: BoxLotReassignmentPayload;
    }) =>
      (
        await api.post<Box>(
          `/boxes/${input.boxId}/reassign-lot`,
          input.payload,
        )
      ).data,
    onSettled: (box, _error, input) => {
      invalidateLotState(qc, [
        input.sourceLotId,
        input.payload.lot_id,
        ...(box ? [box.lot_id] : []),
      ]);
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.box(input.boxId) });
      qc.invalidateQueries({ queryKey: queryKeys.boxEvents(input.boxId) });
      qc.invalidateQueries({ queryKey: ["requests"] });
    },
  });
}

export function useCreateBox() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      box_number: string;
      lot?: string;
      lot_id?: number;
      pallet_number?: string | null;
      pallet_id?: number | null;
      contents?: string;
      files?: FileInput[];
      warehouse_id: number;
      note?: string;
    }) =>
      (
        await api.post<Box | StagedReceiptResult>(
          "/boxes",
          stripGeneratedBarcodeFields(input),
        )
      ).data,
    onSuccess: () => {
      invalidatePalletState(qc);
      invalidateLotState(qc);
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
    },
  });
}

export function useUpdateBox() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      patch: Partial<{
        status: Box["status"];
        warehouse_id: number;
        contents: string;
        files: FileInput[];
        note: string;
        pallet_id: number;
        detach_pallet: boolean;
        force: boolean;
      }>;
    }) =>
      (
        await api.patch<BoxUpdateResult>(
          `/boxes/${input.id}`,
          stripGeneratedBarcodeFields(input.patch),
        )
      ).data,
    onSuccess: (data) => {
      invalidatePalletState(qc, data.pallet_id ? [data.pallet_id] : []);
      invalidateLotState(qc, [data.lot_id]);
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.box(data.id) });
      qc.invalidateQueries({ queryKey: queryKeys.boxEvents(data.id) });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
      qc.invalidateQueries({ queryKey: ["return-candidates"] });
    },
  });
}

export function useDeleteBox() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      force?: boolean;
      reason?: string;
    }) => {
      return (
        await api.post<BoxDeleteResult>(`/boxes/${input.id}/delete`, {
          force: !!input.force,
          reason: input.reason,
        })
      ).data;
    },
    onSuccess: () => {
      invalidateLotState(qc);
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
      qc.invalidateQueries({ queryKey: ["return-candidates"] });
    },
  });
}

export function useBulkDeleteBoxes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      box_ids: number[];
      force?: boolean;
      reason?: string;
    }) =>
      (await api.post<BulkDeleteResult>("/boxes/bulk-delete", input)).data,
    onSuccess: () => {
      invalidateLotState(qc);
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
      qc.invalidateQueries({ queryKey: ["return-candidates"] });
    },
  });
}

export function useBulkUpdateBoxes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: BulkBoxUpdate) =>
      (await api.post<BulkResult>("/boxes/bulk", input)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["lots"] });
      qc.invalidateQueries({ queryKey: ["pallets"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
      qc.invalidateQueries({ queryKey: ["box"] });
      qc.invalidateQueries({ queryKey: ["box-events"] });
    },
  });
}

export function useImportBoxes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      file: File;
      warehouse_id?: number;
      restore_archived?: boolean;
    }) => {
      const form = new FormData();
      form.append("file", input.file);
      if (input.warehouse_id !== undefined) {
        form.append("warehouse_id", String(input.warehouse_id));
      }
      if (input.restore_archived) {
        form.append("restore_archived", "true");
      }
      return (
        await api.post<ImportResult>("/boxes/import", form, {
          headers: { "Content-Type": "multipart/form-data" },
        })
      ).data;
    },
    onSuccess: () => {
      invalidateLotState(qc);
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
    },
  });
}

export function usePreviewBoxImportXlsx() {
  return useMutation({
    mutationFn: async (input: { file: File }) => {
      const form = new FormData();
      form.append("file", input.file);
      return (
        await api.post<XlsxPreview>("/boxes/import-preview", form, {
          headers: { "Content-Type": "multipart/form-data" },
        })
      ).data;
    },
  });
}

export function useXlsxMappingTemplates(
  useCase: XlsxMappingUseCase,
  warehouseId?: number,
) {
  return useQuery({
    queryKey: queryKeys.xlsxMappingTemplates(useCase, warehouseId),
    queryFn: async () =>
      (
        await api.get<XlsxMappingTemplate[]>("/xlsx-mapping-templates", {
          params: { use_case: useCase, warehouse_id: warehouseId },
        })
      ).data,
  });
}

export function useSuggestXlsxMappingTemplates() {
  return useMutation({
    mutationFn: async (input: {
      use_case: XlsxMappingUseCase;
      warehouse_id?: number;
      filename: string;
      sheet_name: string;
      header_candidates: string[][];
    }) =>
      (
        await api.post<XlsxMappingSuggestion[]>(
          "/xlsx-mapping-templates/suggestions",
          input,
        )
      ).data,
  });
}

export function useCreateXlsxMappingTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: XlsxMappingTemplateInput) =>
      (await api.post<XlsxMappingTemplate>("/xlsx-mapping-templates", input))
        .data,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["xlsx-mapping-templates"] }),
  });
}

export function useUpdateXlsxMappingTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      templateId: number;
      payload: XlsxMappingTemplateUpdate;
    }) =>
      (
        await api.patch<XlsxMappingTemplate>(
          `/xlsx-mapping-templates/${input.templateId}`,
          input.payload,
        )
      ).data,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["xlsx-mapping-templates"] }),
  });
}

export function useDeleteXlsxMappingTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (templateId: number) => {
      await api.delete(`/xlsx-mapping-templates/${templateId}`);
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["xlsx-mapping-templates"] }),
  });
}

export function useRecordXlsxMappingTemplateUse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (templateId: number) =>
      (
        await api.post<XlsxMappingTemplate>(
          `/xlsx-mapping-templates/${templateId}/use`,
        )
      ).data,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["xlsx-mapping-templates"] }),
  });
}

export function useImportMappedBoxes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      warehouse_id: number;
      items: InboundRequestItemInput[];
      restore_archived?: boolean;
    }) =>
      (
        await api.post<ImportResult>(
          "/boxes/import-mapped",
          stripGeneratedBarcodeFields(input),
        )
      ).data,
    onSuccess: () => {
      invalidateLotState(qc);
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
    },
  });
}

// --- box order / return requests ------------------------------------------

export function useRequests(
  filters: RequestFilters,
  page: number,
  pageSize: number,
) {
  return useQuery({
    queryKey: queryKeys.requests(filters, page, pageSize),
    queryFn: async () =>
      (
        await api.get<Page<BoxRequest>>(
          `/requests${buildQueryString({
            ...filters,
            page,
            page_size: pageSize,
          })}`,
        )
      ).data,
    placeholderData: (prev) => prev,
  });
}

export function useRequestReconciliation(
  filters: RequestReportFilters,
  page: number,
  pageSize: number,
) {
  return useQuery({
    queryKey: queryKeys.requestReconciliation(filters, page, pageSize),
    queryFn: async () =>
      (
        await api.get<RequestReconciliation>(
          `/requests/reconciliation${buildQueryString({
            ...filters,
            page,
            page_size: pageSize,
          })}`,
        )
      ).data,
    placeholderData: (previous) => previous,
  });
}

export function useRequestAnalytics(filters: RequestReportFilters) {
  const analyticsFilters = {
    warehouse_id: filters.warehouse_id,
    assigned_mover_user_id: filters.assigned_mover_user_id,
    from_at: filters.from_at,
    to_at: filters.to_at,
  };
  return useQuery({
    queryKey: queryKeys.requestAnalytics(analyticsFilters),
    queryFn: async () =>
      (
        await api.get<RequestAnalytics>(
          `/requests/analytics${buildQueryString(analyticsFilters)}`,
        )
      ).data,
  });
}

export function useRequest(id: number | undefined) {
  return useQuery({
    queryKey: id ? queryKeys.request(id) : ["request", "noop"],
    queryFn: async () => (await api.get<BoxRequest>(`/requests/${id}`)).data,
    enabled: !!id,
  });
}

export function useRequestEvents(id: number | undefined) {
  return useQuery({
    queryKey: id ? queryKeys.requestEvents(id) : ["request-events", "noop"],
    queryFn: async () =>
      (await api.get<RequestEvent[]>(`/requests/${id}/events`)).data,
    enabled: !!id,
  });
}

export function useRequestDocuments(id: number | undefined) {
  return useQuery({
    queryKey: id
      ? queryKeys.requestDocuments(id)
      : ["request-documents", "noop"],
    queryFn: async () =>
      (await api.get<RequestDocument[]>(`/requests/${id}/documents`)).data,
    enabled: !!id,
  });
}

export function useRequestComments(id: number | undefined) {
  return useQuery({
    queryKey: id ? queryKeys.requestComments(id) : ["request-comments", "noop"],
    queryFn: async () =>
      (await api.get<RequestComment[]>(`/requests/${id}/comments`)).data,
    enabled: !!id,
  });
}

export function useRequestAttachments(id: number | undefined) {
  return useQuery({
    queryKey: id
      ? queryKeys.requestAttachments(id)
      : ["request-attachments", "noop"],
    queryFn: async () =>
      (await api.get<RequestAttachment[]>(`/requests/${id}/attachments`)).data,
    enabled: !!id,
  });
}

export function useRequestAssignees(warehouseId: number | undefined) {
  return useQuery({
    queryKey: queryKeys.requestAssignees(warehouseId),
    queryFn: async () =>
      (
        await api.get<RequestAssignee[]>(
          `/requests/assignees${buildQueryString({
            warehouse_id: warehouseId,
          })}`,
        )
      ).data,
    enabled: warehouseId !== undefined,
  });
}

export function useRequestSuggestion(
  warehouseId: number | undefined,
  direction: RequestDirection,
) {
  return useQuery({
    queryKey: queryKeys.requestSuggestion(warehouseId, direction),
    queryFn: async () =>
      (
        await api.get<RequestSuggestion>(
          `/requests/suggestion${buildQueryString({
            warehouse_id: warehouseId,
            direction,
          })}`,
        )
      ).data,
    enabled: warehouseId !== undefined,
  });
}

export function useReturnSources(warehouseId: number | undefined) {
  return useQuery({
    queryKey: queryKeys.returnSources(warehouseId),
    queryFn: async () =>
      (
        await api.get<ReturnSource[]>(
          `/requests/return-sources${buildQueryString({
            warehouse_id: warehouseId,
          })}`,
        )
      ).data,
    enabled: warehouseId !== undefined,
  });
}

export function useReturnCandidates(
  sourceInboundRequestId: number | undefined,
) {
  return useQuery({
    queryKey: queryKeys.returnCandidates(sourceInboundRequestId),
    queryFn: async () =>
      (
        await api.get<ReturnCandidate[]>(
          `/requests/${sourceInboundRequestId}/return-candidates`,
        )
      ).data,
    enabled: sourceInboundRequestId !== undefined,
  });
}

export function useInboundCompletionPreview() {
  return useMutation({
    mutationFn: async (input: {
      id: number;
      payload: InboundCompletionPreviewRequest;
    }) =>
      (
        await api.post<InboundCompletionPreview>(
          `/requests/${input.id}/inbound-completion-preview`,
          stripGeneratedBarcodeFields(input.payload),
        )
      ).data,
  });
}

function invalidateRequestQueries(
  qc: ReturnType<typeof useQueryClient>,
  id?: number,
) {
  qc.invalidateQueries({ queryKey: ["requests"] });
  qc.invalidateQueries({ queryKey: ["request-suggestion"] });
  qc.invalidateQueries({ queryKey: ["return-sources"] });
  qc.invalidateQueries({ queryKey: ["return-candidates"] });
  if (id !== undefined) {
    qc.invalidateQueries({ queryKey: queryKeys.request(id) });
    qc.invalidateQueries({ queryKey: queryKeys.requestEvents(id) });
    qc.invalidateQueries({ queryKey: queryKeys.requestDocuments(id) });
    qc.invalidateQueries({ queryKey: queryKeys.requestComments(id) });
    qc.invalidateQueries({ queryKey: queryKeys.requestAttachments(id) });
  }
}

export function useCreateRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: CreateBoxRequestInput) =>
      (await api.post<BoxRequest>("/requests", input)).data,
    onSuccess: (data) => invalidateRequestQueries(qc, data.id),
  });
}

type RequestActionInput =
  | {
      id: number;
      expectedVersion: number;
      action:
        | "approve"
        | "prepare"
        | "mark-ready"
        | "start-transit"
        | "mark-arrived";
    }
  | {
      id: number;
      expectedVersion: number;
      action: "reject";
      body: { reason: string };
    }
  | {
      id: number;
      expectedVersion: number;
      action: "cancel";
      body: { reason?: string };
    }
  | {
      id: number;
      expectedVersion: number;
      action: "hold";
      body: { reason: string };
    }
  | {
      id: number;
      expectedVersion: number;
      action: "resume" | "retry-transport";
      body: { resolution: string };
    }
  | {
      id: number;
      expectedVersion: number;
      action: "reschedule";
      body: {
        reason: string;
        revised_window_start: string;
        revised_window_end: string;
      };
    }
  | {
      id: number;
      expectedVersion: number;
      action: "report-failed-delivery";
      body: {
        reason: string;
        revised_window_start?: string;
        revised_window_end?: string;
      };
    }
  | {
      id: number;
      expectedVersion: number;
      action: "complete";
      body: {
        inbound_items?: InboundRequestItemInput[];
        accept_existing_received_boxes?: boolean;
        accept_file_moves?: boolean;
        inbound_impact_signature?: string | null;
        collected_box_ids?: number[];
        discrepancies?: RequestDiscrepancyInput[];
        discrepancy_reason?: string;
        idempotency_key: string;
      };
    };

export function useRequestAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: RequestActionInput) => {
      const body = stripGeneratedBarcodeFields({
        ...("body" in input ? input.body : {}),
        expected_version: input.expectedVersion,
      });
      return (
        await api.post<BoxRequest>(
          `/requests/${input.id}/${input.action}`,
          body,
        )
      ).data;
    },
    onSuccess: (data) => {
      invalidateRequestQueries(qc, data.id);
      invalidateLotState(qc);
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
  });
}

export function useUpdateRequestCoordination() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      expectedVersion: number;
      patch: Partial<{
        priority: BoxRequest["priority"];
        requested_date: string | null;
        scheduled_window_start: string | null;
        scheduled_window_end: string | null;
        sla_deadline: string | null;
        assigned_mover_user_id: number | null;
        destination_contact: string | null;
        internal_location: string | null;
        special_handling_instructions: string | null;
      }>;
    }) =>
      (
        await api.patch<BoxRequest>(`/requests/${input.id}/coordination`, {
          ...input.patch,
          expected_version: input.expectedVersion,
        })
      ).data,
    onSuccess: (data) => invalidateRequestQueries(qc, data.id),
    onError: (_error, input) => invalidateRequestQueries(qc, input.id),
  });
}

export function useAddRequestComment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      requestId: number;
      expectedVersion: number;
      body: string;
    }) =>
      (
        await api.post<RequestComment>(`/requests/${input.requestId}/comments`, {
          expected_version: input.expectedVersion,
          body: input.body,
        })
      ).data,
    onSettled: (_data, _error, input) =>
      invalidateRequestQueries(qc, input.requestId),
  });
}

export function useUploadRequestAttachment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      requestId: number;
      expectedVersion: number;
      file: File;
    }) => {
      const form = new FormData();
      form.append("file", input.file);
      form.append("expected_version", String(input.expectedVersion));
      return (
        await api.post<RequestAttachment>(
          `/requests/${input.requestId}/attachments`,
          form,
          { headers: { "Content-Type": "multipart/form-data" } },
        )
      ).data;
    },
    onSettled: (_data, _error, input) =>
      invalidateRequestQueries(qc, input.requestId),
  });
}

export function useDownloadRequestAttachment() {
  return useMutation({
    mutationFn: async (input: {
      requestId: number;
      attachmentId: number;
      filename: string;
    }) => {
      const response = await api.get<Blob>(
        `/requests/${input.requestId}/attachments/${input.attachmentId}/download`,
        { responseType: "blob" },
      );
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = input.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    },
  });
}

export function useUploadRequestDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      requestId: number;
      file: File;
      documentType: RequestDocumentType;
      erpReference: string;
      expectedVersion: number;
    }) => {
      const form = new FormData();
      form.append("file", input.file);
      form.append("document_type", input.documentType);
      form.append("erp_reference", input.erpReference);
      form.append("expected_version", String(input.expectedVersion));
      return (
        await api.post<RequestDocument>(
          `/requests/${input.requestId}/documents`,
          form,
          { headers: { "Content-Type": "multipart/form-data" } },
        )
      ).data;
    },
    onSuccess: (_data, input) => invalidateRequestQueries(qc, input.requestId),
    onError: (_error, input) => invalidateRequestQueries(qc, input.requestId),
  });
}

export function useSubmitFollowUpDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      requestId: number;
      expectedVersion: number;
      boxIds: number[];
    }) =>
      (
        await api.post<BoxRequest>(`/requests/${input.requestId}/submit-draft`, {
          expected_version: input.expectedVersion,
          box_ids: input.boxIds,
        })
      ).data,
    onSuccess: (data) => invalidateRequestQueries(qc, data.id),
    onError: (_error, input) => invalidateRequestQueries(qc, input.requestId),
  });
}

export function useRequestDiscrepancies(id: number | undefined) {
  return useQuery({
    queryKey: id ? ["request-discrepancies", id] : ["request-discrepancies", "noop"],
    queryFn: async () =>
      (await api.get<RequestDiscrepancy[]>(`/requests/${id}/discrepancies`)).data,
    enabled: !!id,
  });
}

export function useUploadDiscrepancyPhoto() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      requestId: number;
      discrepancyId: number;
      expectedVersion: number;
      file: File;
    }) => {
      const form = new FormData();
      form.append("file", input.file);
      form.append("expected_version", String(input.expectedVersion));
      return (
        await api.post<RequestDiscrepancyPhoto>(
          `/requests/${input.requestId}/discrepancies/${input.discrepancyId}/photos`,
          form,
          { headers: { "Content-Type": "multipart/form-data" } },
        )
      ).data;
    },
    onSettled: (_data, _error, input) => {
      invalidateRequestQueries(qc, input.requestId);
      qc.invalidateQueries({ queryKey: ["request-discrepancies", input.requestId] });
    },
  });
}

export function useDownloadDiscrepancyPhoto() {
  return useMutation({
    mutationFn: async (input: {
      requestId: number;
      discrepancyId: number;
      photoId: number;
      filename: string;
    }) => {
      const response = await api.get<Blob>(
        `/requests/${input.requestId}/discrepancies/${input.discrepancyId}/photos/${input.photoId}/download`,
        { responseType: "blob", headers: { "Cache-Control": "no-cache" } },
      );
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = input.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    },
  });
}

export function usePreviewInboundXlsx() {
  return useMutation({
    mutationFn: async (input: { requestId: number; file: File }) => {
      const form = new FormData();
      form.append("file", input.file);
      return (
        await api.post<XlsxPreview>(
          `/requests/${input.requestId}/inbound-xlsx-preview`,
          form,
          { headers: { "Content-Type": "multipart/form-data" } },
        )
      ).data;
    },
  });
}

export function useDownloadRequestDocument() {
  return useMutation({
    mutationFn: async (input: {
      requestId: number;
      documentId: number;
      filename: string;
    }) => {
      // Use POST so an older installed service worker cannot satisfy the
      // download from its generic GET API cache.
      const response = await api.post<Blob>(
        `/requests/${input.requestId}/documents/${input.documentId}/download`,
        null,
        {
          responseType: "blob",
          headers: { "Cache-Control": "no-cache" },
        },
      );
      const responseContentType = response.headers["content-type"];
      const contentType =
        typeof responseContentType === "string"
          ? responseContentType
          : response.data.type || "application/octet-stream";
      const blob = new Blob([response.data], { type: contentType });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = input.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // Revoking synchronously can invalidate the blob while the browser's
      // download/PDF viewer is still consuming it, producing a blank or
      // truncated file. Keep it alive long enough for the download to start.
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    },
  });
}

// --- employees + productivity ---------------------------------------------

function invalidateProductivity(qc: ReturnType<typeof useQueryClient>) {
  // Mutations on entries or roster touch every productivity-shaped view:
  // entry tables, daily/weekly summaries, and the dashboard cards. We
  // invalidate broadly rather than surgically because the shared key
  // prefix keeps the request count small in practice.
  qc.invalidateQueries({ queryKey: ["productivity-entries"] });
  qc.invalidateQueries({ queryKey: ["productivity-daily"] });
  qc.invalidateQueries({ queryKey: ["productivity-weekly"] });
  qc.invalidateQueries({ queryKey: ["productivity-monthly"] });
  qc.invalidateQueries({ queryKey: ["productivity-three-month"] });
  qc.invalidateQueries({ queryKey: ["employee-averages"] });
  qc.invalidateQueries({ queryKey: queryKeys.dashboard });
}

// 500 matches the API's hard cap (api/app/routers/employees.py). Dropdown
// callers want every employee in the warehouse without manual paging;
// the Settings page passes its own (smaller) pageSize plus a real page.
const DEFAULT_EMPLOYEES_PAGE_SIZE = 500;

export function useEmployees(
  warehouseId?: number,
  includeInactive: boolean = false,
  opts: { page?: number; pageSize?: number } = {},
) {
  const page = opts.page ?? 1;
  const pageSize = opts.pageSize ?? DEFAULT_EMPLOYEES_PAGE_SIZE;
  return useQuery({
    queryKey: queryKeys.employees(warehouseId, includeInactive, page, pageSize),
    queryFn: async () => {
      const qs = buildQueryString({
        warehouse_id: warehouseId,
        include_inactive: includeInactive,
        page,
        page_size: pageSize,
      });
      return (await api.get<Page<Employee>>(`/employees${qs}`)).data;
    },
    placeholderData: (prev) => prev,
  });
}

export function useCreateEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      warehouse_id: number;
      full_name: string;
      default_hours_per_day?: number;
      excluded_from_metrics?: boolean;
    }) => (await api.post<Employee>("/employees", input)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["employees"] });
      invalidateProductivity(qc);
    },
  });
}

export function useUpdateEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      patch: {
        full_name?: string;
        default_hours_per_day?: number;
        is_active?: boolean;
        excluded_from_metrics?: boolean;
        warehouse_id?: number;
      };
    }) =>
      (await api.patch<Employee>(`/employees/${input.id}`, input.patch)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["employees"] });
      invalidateProductivity(qc);
    },
  });
}

export function useDeleteEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/employees/${id}`);
      return id;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["employees"] });
      invalidateProductivity(qc);
    },
  });
}

export function usePreviewEmployeeImport() {
  return useMutation({
    mutationFn: async (file: File) => {
      const data = new FormData();
      data.append("file", file);
      return (
        await api.post<XlsxPreview>("/employees/import-preview", data, {
          headers: { "Content-Type": "multipart/form-data" },
        })
      ).data;
    },
  });
}

export function useImportMappedEmployees() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      warehouse_id: number;
      items: EmployeeImportItem[];
    }) =>
      (await api.post<EmployeeImportResult>("/employees/import-mapped", input))
        .data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["employees"] });
      invalidateProductivity(qc);
    },
  });
}

export function useProductivityEntries(filters: ProductivityEntryFilters) {
  return useQuery({
    queryKey: queryKeys.productivityEntries(filters),
    queryFn: async () =>
      (
        await api.get<ProductivityEntry[]>(
          `/productivity/entries${buildQueryString(filters)}`,
        )
      ).data,
  });
}

export function useUpsertProductivityEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      employee_id: number;
      entry_date: string;
      pages: number;
      hours_worked: number;
      note?: string;
      excluded_from_metrics?: boolean;
    }) =>
      (await api.post<ProductivityEntry>("/productivity/entries", input)).data,
    onSuccess: () => invalidateProductivity(qc),
  });
}

export function useDeleteProductivityEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/productivity/entries/${id}`);
      return id;
    },
    onSuccess: () => invalidateProductivity(qc),
  });
}

export function useProductivityDailySummary(
  warehouseId?: number,
  date?: string,
) {
  return useQuery({
    queryKey: queryKeys.productivityDaily(warehouseId, date),
    queryFn: async () =>
      (
        await api.get<ProductivitySummary>(
          `/productivity/summary${buildQueryString({
            warehouse_id: warehouseId,
            date,
          })}`,
        )
      ).data,
    refetchInterval: 60_000,
  });
}

export function useProductivityWeeklySummary(
  warehouseId?: number,
  weekStart?: string,
) {
  return useQuery({
    queryKey: queryKeys.productivityWeekly(warehouseId, weekStart),
    queryFn: async () =>
      (
        await api.get<ProductivitySummary>(
          `/productivity/summary/weekly${buildQueryString({
            warehouse_id: warehouseId,
            week_start: weekStart,
          })}`,
        )
      ).data,
    refetchInterval: 60_000,
  });
}

export function useProductivityMonthlySummary(
  warehouseId?: number,
  month?: string,
) {
  return useQuery({
    queryKey: queryKeys.productivityMonthly(warehouseId, month),
    queryFn: async () =>
      (
        await api.get<ProductivitySummary>(
          `/productivity/summary/monthly${buildQueryString({
            warehouse_id: warehouseId,
            month,
          })}`,
        )
      ).data,
    refetchInterval: 60_000,
  });
}

export function useProductivityThreeMonthSummary(
  warehouseId?: number,
  anchorDate?: string,
) {
  return useQuery({
    queryKey: queryKeys.productivityThreeMonth(warehouseId, anchorDate),
    queryFn: async () =>
      (
        await api.get<ProductivitySummary>(
          `/productivity/summary/three-month${buildQueryString({
            warehouse_id: warehouseId,
            anchor_date: anchorDate,
          })}`,
        )
      ).data,
    refetchInterval: 60_000,
  });
}

export function useEmployeeAverages(warehouseId: number, anchorDate?: string) {
  return useQuery({
    queryKey: queryKeys.employeeAverages(warehouseId, anchorDate),
    queryFn: async () =>
      (
        await api.get<EmployeeAverages>(
          `/productivity/employee-averages${buildQueryString({
            warehouse_id: warehouseId,
            anchor_date: anchorDate,
          })}`,
        )
      ).data,
    refetchInterval: 60_000,
  });
}
