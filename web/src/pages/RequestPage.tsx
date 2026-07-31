import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  ChevronLeft,
  ChevronRight,
  Plus,
  Truck,
} from "lucide-react";
import {
  useCreateRequest,
  useMe,
  useRequests,
  useRequestSuggestion,
  useReturnCandidates,
  useReturnSources,
  useWarehouses,
} from "@/api/hooks";
import type {
  BoxRequest,
  RequestDirection,
  RequestFilters,
  RequestStatus,
} from "@/api/types";
import {
  REQUEST_DIRECTION_LABEL,
  REQUEST_STATUS_LABEL,
  RequestStatusBadge,
} from "@/components/RequestStatusBadge";
import {
  canSubmitReturnSelection,
  returnCandidateIds,
  returnSourceLabel,
  toggleReturnBox,
} from "@/pages/returnSelection";

const PAGE_SIZE = 25;
const DIRECTIONS: RequestDirection[] = ["inbound", "return"];
const STATUSES: RequestStatus[] = [
  "submitted",
  "approved",
  "in_transit",
  "completed",
  "rejected",
  "cancelled",
];

function isDirection(value: string | null): value is RequestDirection {
  return DIRECTIONS.includes(value as RequestDirection);
}

function isStatus(value: string | null): value is RequestStatus {
  return STATUSES.includes(value as RequestStatus);
}

export function RequestPage() {
  const [params, setParams] = useSearchParams();
  const warehouses = useWarehouses(true);
  const me = useMe();
  const canCreate = !!me.data;
  const isMover = me.data?.role === "warehouse_mover";
  const [showCreate, setShowCreate] = useState(false);

  const filters = useMemo<RequestFilters>(() => {
    const warehouseId = Number(params.get("warehouse_id"));
    const direction = params.get("direction");
    const status = params.get("status");
    return {
      warehouse_id:
        Number.isInteger(warehouseId) && warehouseId > 0
          ? warehouseId
          : undefined,
      direction: isDirection(direction) ? direction : undefined,
      status: isStatus(status) ? status : undefined,
    };
  }, [params]);
  const rawPage = Number(params.get("page"));
  const page = Number.isInteger(rawPage) && rawPage > 0 ? rawPage : 1;
  const requests = useRequests(filters, page, PAGE_SIZE);
  const totalPages = Math.max(
    1,
    Math.ceil((requests.data?.total ?? 0) / PAGE_SIZE),
  );

  function setParam(name: string, value?: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    next.delete("page");
    setParams(next);
  }

  function setPage(nextPage: number) {
    const next = new URLSearchParams(params);
    if (nextPage <= 1) next.delete("page");
    else next.set("page", String(nextPage));
    setParams(next);
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {isMover ? "Movement queue" : "Box requests"}
          </h1>
          <p className="text-sm text-slate-500">
            {isMover
              ? "Dispatch approved requests and complete deliveries or returns."
              : "Order boxes into a warehouse or arrange eligible box returns."}
          </p>
        </div>
        {canCreate && (
          <button
            type="button"
            className="btn-primary"
            onClick={() => setShowCreate(true)}
          >
            <Plus className="h-4 w-4" /> New request
          </button>
        )}
      </header>

      {isMover && (
        <section
          className="card flex flex-col gap-3 border-brand-200 bg-brand-50/60 p-4 sm:flex-row sm:items-center"
          aria-label="Mover queue shortcuts"
        >
          <Truck className="h-5 w-5 flex-none text-brand-700" />
          <p className="flex-1 text-sm text-brand-900">
            Approved requests are ready to dispatch. In-transit requests are
            ready for confirmation at their destination.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setParam("status", "approved")}
            >
              Ready to dispatch
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setParam("status", "in_transit")}
            >
              In transit
            </button>
          </div>
        </section>
      )}

      <section className="card card-pad grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="text-xs text-slate-500">Warehouse</span>
          <select
            className="input"
            value={filters.warehouse_id ?? ""}
            onChange={(event) =>
              setParam("warehouse_id", event.target.value || undefined)
            }
          >
            <option value="">All warehouses</option>
            {warehouses.data?.map((warehouse) => (
              <option key={warehouse.id} value={warehouse.id}>
                {warehouse.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Direction</span>
          <select
            className="input"
            value={filters.direction ?? ""}
            onChange={(event) =>
              setParam("direction", event.target.value || undefined)
            }
          >
            <option value="">All directions</option>
            {DIRECTIONS.map((direction) => (
              <option key={direction} value={direction}>
                {REQUEST_DIRECTION_LABEL[direction]}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Status</span>
          <select
            className="input"
            value={filters.status ?? ""}
            onChange={(event) =>
              setParam("status", event.target.value || undefined)
            }
          >
            <option value="">All statuses</option>
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {REQUEST_STATUS_LABEL[status]}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section className="card overflow-hidden">
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-4 py-2.5">Request</th>
                <th className="px-4 py-2.5">Warehouse</th>
                <th className="px-4 py-2.5">Direction</th>
                <th className="px-4 py-2.5 text-right">Quantity</th>
                <th className="px-4 py-2.5">Requester</th>
                <th className="px-4 py-2.5">Status</th>
                <th className="px-4 py-2.5">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {requests.isLoading && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-slate-400">
                    Loading…
                  </td>
                </tr>
              )}
              {!requests.isLoading && requests.data?.items.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-slate-400">
                    No requests match these filters.
                  </td>
                </tr>
              )}
              {requests.data?.items.map((request) => (
                <RequestRow
                  key={request.id}
                  request={request}
                  warehouseName={
                    warehouses.data?.find(
                      (warehouse) => warehouse.id === request.warehouse_id,
                    )?.name ?? `#${request.warehouse_id}`
                  }
                />
              ))}
            </tbody>
          </table>
        </div>

        <div className="divide-y divide-slate-100 md:hidden">
          {requests.isLoading && (
            <p className="px-4 py-8 text-center text-sm text-slate-400">
              Loading…
            </p>
          )}
          {!requests.isLoading && requests.data?.items.length === 0 && (
            <p className="px-4 py-8 text-center text-sm text-slate-400">
              No requests match these filters.
            </p>
          )}
          {requests.data?.items.map((request) => (
            <RequestCard
              key={request.id}
              request={request}
              warehouseName={
                warehouses.data?.find(
                  (warehouse) => warehouse.id === request.warehouse_id,
                )?.name ?? `#${request.warehouse_id}`
              }
            />
          ))}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
          <span>
            Page {page} of {totalPages} · {requests.data?.total ?? 0} total
          </span>
          <div className="flex gap-1">
            <button
              type="button"
              className="btn-ghost"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
            >
              <ChevronLeft className="h-4 w-4" /> Prev
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
            >
              Next <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </footer>
      </section>

      {showCreate && (
        <CreateRequestDialog onClose={() => setShowCreate(false)} />
      )}
    </div>
  );
}

function RequestRow({
  request,
  warehouseName,
}: {
  request: BoxRequest;
  warehouseName: string;
}) {
  const DirectionIcon =
    request.direction === "inbound" ? ArrowDownToLine : ArrowUpFromLine;
  return (
    <tr className="hover:bg-slate-50">
      <td className="px-4 py-3 font-medium">
        <Link
          className="text-brand-700 hover:underline"
          to={`/requests/${request.id}`}
        >
          #{request.id}
        </Link>
      </td>
      <td className="px-4 py-3">{warehouseName}</td>
      <td className="px-4 py-3">
        <span className="inline-flex items-center gap-1.5">
          <DirectionIcon className="h-4 w-4 text-slate-400" />
          {REQUEST_DIRECTION_LABEL[request.direction]}
        </span>
      </td>
      <td className="px-4 py-3 text-right tabular-nums">{request.quantity}</td>
      <td className="px-4 py-3">{request.requester_name ?? "—"}</td>
      <td className="px-4 py-3">
        <RequestStatusBadge
          status={request.status}
          direction={request.direction}
        />
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-slate-500">
        {new Date(request.updated_at).toLocaleString()}
      </td>
    </tr>
  );
}

function RequestCard({
  request,
  warehouseName,
}: {
  request: BoxRequest;
  warehouseName: string;
}) {
  return (
    <Link
      to={`/requests/${request.id}`}
      className="block px-4 py-3 hover:bg-slate-50"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-semibold text-brand-700">Request #{request.id}</div>
          <div className="mt-0.5 text-sm text-slate-700">
            {REQUEST_DIRECTION_LABEL[request.direction]} · {request.quantity} boxes
          </div>
        </div>
        <RequestStatusBadge
          status={request.status}
          direction={request.direction}
        />
      </div>
      <div className="mt-2 flex items-center justify-between text-xs text-slate-500">
        <span>{warehouseName}</span>
        <span>{new Date(request.updated_at).toLocaleString()}</span>
      </div>
    </Link>
  );
}

function CreateRequestDialog({ onClose }: { onClose: () => void }) {
  const warehouses = useWarehouses();
  const create = useCreateRequest();
  const [direction, setDirection] = useState<RequestDirection>("inbound");
  const [warehouseId, setWarehouseId] = useState<number | undefined>();
  const [quantity, setQuantity] = useState(1);
  const [quantityTouched, setQuantityTouched] = useState(false);
  const [sourceInboundRequestId, setSourceInboundRequestId] = useState<
    number | undefined
  >();
  const [selectedBoxIds, setSelectedBoxIds] = useState<number[]>([]);
  const [initializedSourceId, setInitializedSourceId] = useState<
    number | undefined
  >();
  const [error, setError] = useState<string | null>(null);
  const suggestion = useRequestSuggestion(
    direction === "inbound" ? warehouseId : undefined,
    direction,
  );
  const returnSources = useReturnSources(
    direction === "return" ? warehouseId : undefined,
  );
  const returnCandidates = useReturnCandidates(
    direction === "return" ? sourceInboundRequestId : undefined,
  );

  useEffect(() => {
    setQuantityTouched(false);
    setSourceInboundRequestId(undefined);
    setSelectedBoxIds([]);
    setInitializedSourceId(undefined);
  }, [warehouseId, direction]);

  useEffect(() => {
    if (!quantityTouched && suggestion.data) {
      setQuantity(Math.max(1, suggestion.data.suggested_quantity));
    }
  }, [quantityTouched, suggestion.data]);

  useEffect(() => {
    if (
      sourceInboundRequestId !== undefined &&
      returnCandidates.data &&
      initializedSourceId !== sourceInboundRequestId
    ) {
      setSelectedBoxIds(returnCandidateIds(returnCandidates.data));
      setInitializedSourceId(sourceInboundRequestId);
    }
  }, [initializedSourceId, returnCandidates.data, sourceInboundRequestId]);

  const returnUnavailable =
    direction === "return" &&
    !!returnSources.data &&
    !returnSources.data.some((source) => source.eligible_quantity > 0);

  return (
    <div className="modal-backdrop" role="presentation">
      <div
        className="modal-sheet max-w-3xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-request-title"
      >
        <h2 id="create-request-title" className="text-lg font-semibold">
          New box request
        </h2>
        <form
          className="mt-4 space-y-4"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!warehouseId) {
              setError("Choose a warehouse.");
              return;
            }
            setError(null);
            try {
              if (direction === "return") {
                if (
                  !canSubmitReturnSelection(
                    sourceInboundRequestId,
                    selectedBoxIds,
                  )
                ) {
                  setError(
                    "Choose a completed inbound order and at least one box.",
                  );
                  return;
                }
                await create.mutateAsync({
                  direction,
                  warehouse_id: warehouseId,
                  quantity: selectedBoxIds.length,
                  source_inbound_request_id: sourceInboundRequestId!,
                  box_ids: selectedBoxIds,
                });
              } else {
                await create.mutateAsync({
                  direction,
                  warehouse_id: warehouseId,
                  quantity,
                });
              }
              onClose();
            } catch (caught) {
              setError(apiError(caught, "Failed to create request."));
            }
          }}
        >
          <fieldset>
            <legend className="text-xs text-slate-500">Direction</legend>
            <div className="mt-1 grid grid-cols-2 gap-2">
              {DIRECTIONS.map((value) => {
                const Icon =
                  value === "inbound" ? ArrowDownToLine : ArrowUpFromLine;
                return (
                  <label
                    key={value}
                    className={`flex cursor-pointer items-center gap-2 rounded-lg border p-3 text-sm ${
                      direction === value
                        ? "border-brand-500 bg-brand-50 text-brand-800"
                        : "border-slate-200 bg-white text-slate-700"
                    }`}
                  >
                    <input
                      type="radio"
                      name="direction"
                      value={value}
                      checked={direction === value}
                      onChange={() => setDirection(value)}
                    />
                    <Icon className="h-4 w-4" />
                    {REQUEST_DIRECTION_LABEL[value]}
                  </label>
                );
              })}
            </div>
          </fieldset>

          <label className="block">
            <span className="text-xs text-slate-500">Warehouse</span>
            <select
              required
              className="input"
              value={warehouseId ?? ""}
              onChange={(event) =>
                setWarehouseId(
                  event.target.value ? Number(event.target.value) : undefined,
                )
              }
            >
              <option value="">Choose a warehouse</option>
              {warehouses.data?.map((warehouse) => (
                <option key={warehouse.id} value={warehouse.id}>
                  {warehouse.name}
                </option>
              ))}
            </select>
          </label>

          {warehouseId && direction === "inbound" && (
            <SuggestionPanel
              loading={suggestion.isLoading}
              direction={direction}
              suggestion={suggestion.data}
            />
          )}

          {direction === "inbound" ? (
            <label className="block">
              <span className="text-xs text-slate-500">Quantity</span>
              <input
                required
                className="input"
                type="number"
                min={1}
                max={5000}
                value={quantity}
                onChange={(event) => {
                  setQuantityTouched(true);
                  setQuantity(Number(event.target.value));
                }}
              />
            </label>
          ) : (
            warehouseId && (
              <section className="space-y-3">
                <label className="block">
                  <span className="text-xs text-slate-500">
                    Completed inbound order
                  </span>
                  <select
                    required
                    className="input"
                    value={sourceInboundRequestId ?? ""}
                    disabled={returnSources.isLoading}
                    onChange={(event) => {
                      const nextId = event.target.value
                        ? Number(event.target.value)
                        : undefined;
                      setSourceInboundRequestId(nextId);
                      setSelectedBoxIds([]);
                      setInitializedSourceId(undefined);
                      setError(null);
                    }}
                  >
                    <option value="">
                      {returnSources.isLoading
                        ? "Loading completed orders…"
                        : "Choose an inbound order"}
                    </option>
                    {returnSources.data?.map((source) => (
                      <option
                        key={source.id}
                        value={source.id}
                        disabled={source.eligible_quantity === 0}
                      >
                        {returnSourceLabel(source.origin)} #{source.id} ·{" "}
                        {source.eligible_quantity} of{" "}
                        {source.delivered_quantity} boxes ready ·{" "}
                        {new Date(source.completed_at).toLocaleDateString()}
                      </option>
                    ))}
                  </select>
                </label>

                {sourceInboundRequestId && (
                  <div className="rounded-lg border border-slate-200">
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2">
                      <div>
                        <h3 className="text-sm font-medium text-slate-800">
                          Boxes to return
                        </h3>
                        <p className="text-xs text-slate-500">
                          All Ready to Return boxes are selected initially.
                          Clear any boxes that should remain for a later return.
                        </p>
                      </div>
                      <div className="flex items-center gap-3 text-xs">
                        <button
                          type="button"
                          className="text-brand-700 underline hover:text-brand-800"
                          disabled={!returnCandidates.data?.length}
                          onClick={() =>
                            setSelectedBoxIds(
                              returnCandidateIds(returnCandidates.data ?? []),
                            )
                          }
                        >
                          Select all
                        </button>
                        <button
                          type="button"
                          className="text-slate-600 underline hover:text-slate-800"
                          disabled={!selectedBoxIds.length}
                          onClick={() => setSelectedBoxIds([])}
                        >
                          Clear
                        </button>
                        <strong className="tabular-nums text-slate-800">
                          {selectedBoxIds.length} selected
                        </strong>
                      </div>
                    </div>
                    {returnCandidates.isLoading ? (
                      <p className="p-4 text-sm text-slate-500">
                        Loading returnable boxes…
                      </p>
                    ) : returnCandidates.data?.length ? (
                      <div className="max-h-72 overflow-auto">
                        <table className="w-full text-sm">
                          <thead className="sticky top-0 bg-white text-left text-xs uppercase tracking-wider text-slate-500">
                            <tr>
                              <th className="w-10 px-3 py-2">
                                <span className="sr-only">Select</span>
                              </th>
                              <th className="px-3 py-2">Box</th>
                              <th className="px-3 py-2">Lot</th>
                              <th className="px-3 py-2">Contents</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-100">
                            {returnCandidates.data.map((box) => (
                              <tr key={box.box_id}>
                                <td className="px-3 py-2">
                                  <input
                                    type="checkbox"
                                    aria-label={`Return box ${box.box_number}`}
                                    checked={selectedBoxIds.includes(box.box_id)}
                                    onChange={() =>
                                      setSelectedBoxIds((current) =>
                                        toggleReturnBox(current, box.box_id),
                                      )
                                    }
                                  />
                                </td>
                                <td className="px-3 py-2 font-medium">
                                  {box.box_number}
                                </td>
                                <td className="px-3 py-2">{box.lot}</td>
                                <td className="px-3 py-2 text-slate-500">
                                  {box.contents || "—"}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="p-4 text-sm text-amber-700">
                        This inbound order has no unreserved boxes currently
                        Ready to Return.
                      </p>
                    )}
                  </div>
                )}
              </section>
            )
          )}

          {returnUnavailable && (
            <p className="text-sm text-amber-700">
              This warehouse has no boxes currently eligible for return.
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-rose-600">
              {error}
            </p>
          )}
          <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:justify-end">
            <button
              type="button"
              className="btn-secondary"
              disabled={create.isPending}
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={
                create.isPending ||
                !warehouseId ||
                (direction === "inbound"
                  ? quantity < 1
                  : !canSubmitReturnSelection(
                      sourceInboundRequestId,
                      selectedBoxIds,
                    )) ||
                returnUnavailable
              }
            >
              {create.isPending ? "Submitting…" : "Submit request"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function SuggestionPanel({
  loading,
  direction,
  suggestion,
}: {
  loading: boolean;
  direction: RequestDirection;
  suggestion:
    | {
        current_available: number;
        min_inventory: number;
        pending_inbound: number;
        suggested_quantity: number;
        eligible_return: number;
      }
    | undefined;
}) {
  if (loading) {
    return (
      <div className="rounded-lg bg-slate-50 p-3 text-sm text-slate-500">
        Calculating recommendation…
      </div>
    );
  }
  if (!suggestion) return null;
  return (
    <section className="rounded-lg border border-brand-100 bg-brand-50/60 p-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-medium text-brand-900">Recommendation</h3>
        <strong className="text-lg tabular-nums text-brand-800">
          {suggestion.suggested_quantity} boxes
        </strong>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        <SuggestionValue label="Available" value={suggestion.current_available} />
        <SuggestionValue label="Minimum" value={suggestion.min_inventory} />
        <SuggestionValue label="Pending inbound" value={suggestion.pending_inbound} />
        <SuggestionValue
          label={direction === "return" ? "Eligible to return" : "Return eligible"}
          value={suggestion.eligible_return}
        />
      </dl>
    </section>
  );
}

function SuggestionValue({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-slate-500">{label}</dt>
      <dd className="font-medium tabular-nums text-slate-800">{value}</dd>
    </div>
  );
}

function apiError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

