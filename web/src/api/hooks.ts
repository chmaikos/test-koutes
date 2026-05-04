import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, buildQueryString } from "@/api/client";
import type {
  Alert,
  Box,
  BoxEvent,
  BoxFilters,
  BulkBoxUpdate,
  BulkDeleteResult,
  BulkResult,
  DashboardSummary,
  ImportResult,
  Page,
  User,
  Warehouse,
} from "@/api/types";

export const queryKeys = {
  me: ["me"] as const,
  dashboard: ["dashboard"] as const,
  warehouses: ["warehouses"] as const,
  users: ["users"] as const,
  alerts: (open: boolean) => ["alerts", open] as const,
  box: (id: number) => ["box", id] as const,
  boxEvents: (id: number) => ["box-events", id] as const,
  boxes: (filters: BoxFilters, page: number, pageSize: number) =>
    ["boxes", filters, page, pageSize] as const,
};

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
      patch: { role?: User["role"]; is_active?: boolean; role_override?: boolean };
    }) => (await api.patch<User>(`/users/${input.id}`, input.patch)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.users }),
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
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
      owner: string;
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
        owner: string;
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
