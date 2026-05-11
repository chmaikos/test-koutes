import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, buildQueryString } from "@/api/client";
import type {
  Alert,
  AlertDetail,
  AlertRecipients,
  AlertTestEmailResult,
  Box,
  BoxEvent,
  BoxFilters,
  BulkBoxUpdate,
  BulkDeleteResult,
  BulkResult,
  DashboardSummary,
  Employee,
  ImportResult,
  Page,
  ProductivityEntry,
  ProductivitySummary,
  User,
  Warehouse,
} from "@/api/types";

export const queryKeys = {
  me: ["me"] as const,
  dashboard: ["dashboard"] as const,
  warehouses: ["warehouses"] as const,
  users: ["users"] as const,
  alerts: (open: boolean) => ["alerts", open] as const,
  alert: (id: number) => ["alert", id] as const,
  alertRecipients: ["alert-recipients"] as const,
  box: (id: number) => ["box", id] as const,
  boxEvents: (id: number) => ["box-events", id] as const,
  boxes: (filters: BoxFilters, page: number, pageSize: number) =>
    ["boxes", filters, page, pageSize] as const,
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

export function useWarehouses() {
  return useQuery({
    queryKey: queryKeys.warehouses,
    queryFn: async () => (await api.get<Warehouse[]>("/warehouses")).data,
  });
}

export function useUpdateWarehouse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      patch: Partial<Pick<Warehouse, "name" | "min_inventory" | "max_capacity">>;
    }) => (await api.patch<Warehouse>(`/warehouses/${input.id}`, input.patch)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.warehouses });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
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
    }) => (await api.post<Warehouse>("/warehouses", input)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.warehouses });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
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
        warehouse_ids?: number[];
      };
    }) => (await api.patch<User>(`/users/${input.id}`, input.patch)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.users });
      // ACL or opt-out changes shift the alert recipient preview as well.
      qc.invalidateQueries({ queryKey: queryKeys.alertRecipients });
      // ACL changes can flip what the affected user (or the admin themselves
      // when adjusting their own row) sees; refresh warehouse-scoped views.
      qc.invalidateQueries({ queryKey: queryKeys.warehouses });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
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

export function useCreateBox() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      box_number: string;
      lot: string;
      contents?: string;
      warehouse_id: number;
      note?: string;
    }) => (await api.post<Box>("/boxes", input)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
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
        lot: string;
        contents: string;
        note: string;
        force: boolean;
      }>;
    }) => (await api.patch<Box>(`/boxes/${input.id}`, input.patch)).data,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.box(data.id) });
      qc.invalidateQueries({ queryKey: queryKeys.boxEvents(data.id) });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}

export function useDeleteBox() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/boxes/${id}`);
      return id;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}

export function useBulkDeleteBoxes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (box_ids: number[]) =>
      (await api.post<BulkDeleteResult>("/boxes/bulk-delete", { box_ids }))
        .data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
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
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
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
    }) => {
      const form = new FormData();
      form.append("file", input.file);
      if (input.warehouse_id !== undefined) {
        form.append("warehouse_id", String(input.warehouse_id));
      }
      return (
        await api.post<ImportResult>("/boxes/import", form, {
          headers: { "Content-Type": "multipart/form-data" },
        })
      ).data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.invalidateQueries({ queryKey: ["alerts"] });
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
