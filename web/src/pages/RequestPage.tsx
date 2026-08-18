import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  BarChart3,
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
  RequestPriority,
  RequestQueue,
  RequestSuggestion,
  RequestSortField,
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
import { requestLots } from "@/pages/requestLots";
import {
  activeTargetWarehouses,
  defaultTargetWarehouseId,
  requestWarehousePayload,
  requestWarehouseRoute,
  requestWarehouseValidationError,
} from "@/pages/requestWarehouses";

const PAGE_SIZE = 25;
const DIRECTIONS: RequestDirection[] = ["inbound", "return"];
const STATUSES: RequestStatus[] = [
  "draft",
  "submitted",
  "approved",
  "preparing",
  "ready_for_transport",
  "in_transit",
  "awaiting_confirmation",
  "completed",
  "rejected",
  "cancelled",
];
const PRIORITIES: RequestPriority[] = ["low", "normal", "high", "urgent"];
const QUEUES: { value: RequestQueue; label: string }[] = [
  { value: "unassigned", label: "Unassigned" },
  { value: "due_today", label: "Due today" },
  { value: "overdue", label: "Overdue" },
  { value: "ready_for_transport", label: "Ready for transport" },
  { value: "awaiting_confirmation", label: "Awaiting confirmation" },
  { value: "pending_receipt_review", label: "Pending receipt review" },
];
const SORT_FIELDS: { value: RequestSortField; label: string }[] = [
  { value: "created_at", label: "Created" },
  { value: "updated_at", label: "Updated" },
  { value: "priority", label: "Priority" },
  { value: "assignment", label: "Assignment" },
];

function isDirection(value: string | null): value is RequestDirection {
  return DIRECTIONS.includes(value as RequestDirection);
}

function isStatus(value: string | null): value is RequestStatus {
  return STATUSES.includes(value as RequestStatus);
}

function isPriority(value: string | null): value is RequestPriority {
  return PRIORITIES.includes(value as RequestPriority);
}

function isQueue(value: string | null): value is RequestQueue {
  return QUEUES.some((queue) => queue.value === value);
}

function isSort(value: string | null): value is RequestSortField {
  return SORT_FIELDS.some((field) => field.value === value);
}

export function RequestPage() {
  const [params, setParams] = useSearchParams();
  const warehouses = useWarehouses(true);
  const me = useMe();
  const canCreate = !!me.data;
  const isMover =
    me.data?.role === "warehouse_mover" || me.data?.role === "admin";
  const [showCreate, setShowCreate] = useState(false);

  const filters = useMemo<RequestFilters>(() => {
    const warehouseId = Number(params.get("warehouse_id"));
    const direction = params.get("direction");
    const status = params.get("status");
    const priority = params.get("priority");
    const queue = params.get("queue");
    const sortBy = params.get("sort_by");
    return {
      warehouse_id:
        Number.isInteger(warehouseId) && warehouseId > 0
          ? warehouseId
          : undefined,
      direction: isDirection(direction) ? direction : undefined,
      status: isStatus(status) ? status : undefined,
      priority: isPriority(priority) ? priority : undefined,
      queue: isQueue(queue) ? queue : undefined,
      search: params.get("search") || undefined,
      sort_by: isSort(sortBy) ? sortBy : "created_at",
      sort_dir: params.get("sort_dir") === "asc" ? "asc" : "desc",
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
              ? "Prepare, dispatch, recover, and confirm deliveries or returns."
              : "Order boxes into a warehouse or arrange eligible box returns."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link className="btn-secondary" to="/requests/reconciliation">
            <BarChart3 className="h-4 w-4" /> Reconciliation
          </Link>
          {canCreate && (
            <button
              type="button"
              className="btn-primary"
              onClick={() => setShowCreate(true)}
            >
              <Plus className="h-4 w-4" /> New request
            </button>
          )}
        </div>
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
              onClick={() => setParam("status", "awaiting_confirmation")}
            >
              Awaiting confirmation
            </button>
          </div>
        </section>
      )}

      <div className="flex flex-wrap gap-2" aria-label="Request queues">
        {QUEUES.map((queue) => (
          <button
            key={queue.value}
            type="button"
            className={
              filters.queue === queue.value ? "btn-primary" : "btn-secondary"
            }
            onClick={() =>
              setParam(
                "queue",
                filters.queue === queue.value ? undefined : queue.value,
              )
            }
          >
            {queue.label}
          </button>
        ))}
      </div>

      <section className="card card-pad grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
        <label className="block sm:col-span-2">
          <span className="text-xs text-slate-500">Search</span>
          <input
            className="input"
            type="search"
            value={filters.search ?? ""}
            placeholder="Request, person, contact, location…"
            onChange={(event) =>
              setParam("search", event.target.value || undefined)
            }
          />
        </label>
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
        <label className="block">
          <span className="text-xs text-slate-500">Priority</span>
          <select
            className="input"
            value={filters.priority ?? ""}
            onChange={(event) =>
              setParam("priority", event.target.value || undefined)
            }
          >
            <option value="">All priorities</option>
            {PRIORITIES.map((priority) => (
              <option key={priority} value={priority}>
                {priority[0].toUpperCase() + priority.slice(1)}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Sort</span>
          <select
            className="input"
            value={filters.sort_by}
            onChange={(event) => setParam("sort_by", event.target.value)}
          >
            {SORT_FIELDS.map((field) => (
              <option key={field.value} value={field.value}>
                {field.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Sort direction</span>
          <select
            className="input"
            value={filters.sort_dir}
            onChange={(event) => setParam("sort_dir", event.target.value)}
          >
            <option value="desc">Descending</option>
            <option value="asc">Ascending</option>
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
                <th className="px-4 py-2.5">Priority</th>
                <th className="px-4 py-2.5">Assigned</th>
                <th className="px-4 py-2.5">Lot</th>
                <th className="px-4 py-2.5">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {requests.isLoading && (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-slate-400">
                    Loading…
                  </td>
                </tr>
              )}
              {!requests.isLoading && requests.data?.items.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-slate-400">
                    No requests match these filters.
                  </td>
                </tr>
              )}
              {requests.data?.items.map((request) => (
                <RequestRow
                  key={request.id}
                  request={request}
                  warehouseLabel={requestWarehouseRoute(
                    request,
                    warehouses.data ?? [],
                  ).label}
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
              warehouseLabel={requestWarehouseRoute(
                request,
                warehouses.data ?? [],
              ).label}
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
  warehouseLabel,
}: {
  request: BoxRequest;
  warehouseLabel: string;
}) {
  const DirectionIcon =
    request.direction === "inbound" ? ArrowDownToLine : ArrowUpFromLine;
  const lots = requestLots(request);
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
      <td className="px-4 py-3">{warehouseLabel}</td>
      <td className="px-4 py-3">
        <span className="inline-flex items-center gap-1.5">
          <DirectionIcon className="h-4 w-4 text-slate-400" />
          {REQUEST_DIRECTION_LABEL[request.direction]}
        </span>
      </td>
      <td className="px-4 py-3 text-right tabular-nums">{request.quantity}</td>
      <td className="px-4 py-3">
        <PriorityBadge priority={request.priority} />
      </td>
      <td className="px-4 py-3">
        {request.assigned_mover_name ?? (
          <span className="text-amber-700">Unassigned</span>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-slate-600">
        <RequestLotLinks lots={lots} />
      </td>
      <td className="px-4 py-3">
        <RequestStatusBadge
          status={request.status}
          direction={request.direction}
        />
      </td>
    </tr>
  );
}

function RequestCard({
  request,
  warehouseLabel,
}: {
  request: BoxRequest;
  warehouseLabel: string;
}) {
  const lots = requestLots(request);
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
        <span>{warehouseLabel}</span>
        <PriorityBadge priority={request.priority} />
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-slate-500">
        <span>{request.assigned_mover_name ?? "Unassigned"}</span>
        <span className="text-right">
          <RequestLotLinks lots={lots} linked={false} />
        </span>
      </div>
    </Link>
  );
}

function PriorityBadge({ priority }: { priority: RequestPriority }) {
  const classes = {
    low: "bg-slate-100 text-slate-600",
    normal: "bg-blue-50 text-blue-700",
    high: "bg-amber-100 text-amber-800",
    urgent: "bg-rose-100 text-rose-800",
  }[priority];
  return (
    <span className={`badge ${classes}`}>
      {priority[0].toUpperCase() + priority.slice(1)}
    </span>
  );
}

function RequestLotLinks({
  lots,
  linked = true,
}: {
  lots: ReturnType<typeof requestLots>;
  linked?: boolean;
}) {
  if (lots.length === 0) return <span>—</span>;
  return (
    <span className="inline-flex flex-wrap gap-x-1">
      {lots.map((lot, index) => (
        <span key={lot.key}>
          {index > 0 && <span className="text-slate-400">, </span>}
          {lot.id === null || !linked ? (
            lot.name
          ) : (
            <Link
              className="text-brand-700 hover:underline"
              to={`/lots/${lot.id}`}
              onClick={(event) => event.stopPropagation()}
            >
              {lot.name}
            </Link>
          )}
        </span>
      ))}
    </span>
  );
}

function CreateRequestDialog({ onClose }: { onClose: () => void }) {
  const warehouses = useWarehouses();
  const create = useCreateRequest();
  const availableWarehouses = useMemo(
    () => activeTargetWarehouses(warehouses.data ?? []),
    [warehouses.data],
  );
  const [direction, setDirection] = useState<RequestDirection>("inbound");
  const [warehouseId, setWarehouseId] = useState<number | undefined>();
  const [targetWarehouseId, setTargetWarehouseId] = useState<
    number | undefined
  >();
  const [quantity, setQuantity] = useState(1);
  const [priority, setPriority] = useState<RequestPriority>("normal");
  const [destinationContact, setDestinationContact] = useState("");
  const [internalLocation, setInternalLocation] = useState("");
  const [specialInstructions, setSpecialInstructions] = useState("");
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
    setError(null);
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
              setError(
                direction === "return"
                  ? "Choose a source warehouse."
                  : "Choose a warehouse.",
              );
              return;
            }
            const targetError = requestWarehouseValidationError(
              direction,
              targetWarehouseId,
              availableWarehouses,
            );
            if (targetError) {
              setError(targetError);
              return;
            }
            setError(null);
            try {
              if (direction === "return") {
                const warehousePayload = requestWarehousePayload(
                  direction,
                  warehouseId,
                  targetWarehouseId,
                  availableWarehouses,
                );
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
                  ...warehousePayload,
                  quantity: selectedBoxIds.length,
                  source_inbound_request_id: sourceInboundRequestId!,
                  box_ids: selectedBoxIds,
                  priority,
                  destination_contact: destinationContact || undefined,
                  internal_location: internalLocation || undefined,
                  special_handling_instructions:
                    specialInstructions || undefined,
                });
              } else {
                const warehousePayload = requestWarehousePayload(
                  direction,
                  warehouseId,
                  targetWarehouseId,
                  availableWarehouses,
                );
                await create.mutateAsync({
                  direction,
                  ...warehousePayload,
                  quantity,
                  priority,
                  destination_contact: destinationContact || undefined,
                  internal_location: internalLocation || undefined,
                  special_handling_instructions:
                    specialInstructions || undefined,
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
                      onChange={() => {
                        setDirection(value);
                        setTargetWarehouseId(
                          defaultTargetWarehouseId(value, warehouseId),
                        );
                      }}
                    />
                    <Icon className="h-4 w-4" />
                    {REQUEST_DIRECTION_LABEL[value]}
                  </label>
                );
              })}
            </div>
          </fieldset>

          <label className="block">
            <span className="text-xs text-slate-500">
              {direction === "return" ? "Source warehouse" : "Warehouse"}
            </span>
            <select
              required
              className="input"
              value={warehouseId ?? ""}
              onChange={(event) => {
                const nextWarehouseId = event.target.value
                  ? Number(event.target.value)
                  : undefined;
                setWarehouseId(nextWarehouseId);
                setTargetWarehouseId(
                  defaultTargetWarehouseId(direction, nextWarehouseId),
                );
              }}
            >
              <option value="">Choose a warehouse</option>
              {availableWarehouses.map((warehouse) => (
                <option key={warehouse.id} value={warehouse.id}>
                  {warehouse.name}
                </option>
              ))}
            </select>
          </label>

          {direction === "return" && (
            <label className="block">
              <span className="text-xs text-slate-500">Target warehouse</span>
              <select
                required
                className="input"
                value={targetWarehouseId ?? ""}
                onChange={(event) => {
                  setTargetWarehouseId(
                    event.target.value
                      ? Number(event.target.value)
                      : undefined,
                  );
                  setError(null);
                }}
              >
                <option value="">Choose a target warehouse</option>
                {availableWarehouses.map((warehouse) => (
                  <option key={warehouse.id} value={warehouse.id}>
                    {warehouse.name}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-xs text-slate-500">
                Returned boxes will move from the source warehouse to this
                warehouse.
              </span>
            </label>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="text-xs text-slate-500">Priority</span>
              <select
                className="input"
                value={priority}
                onChange={(event) =>
                  setPriority(event.target.value as RequestPriority)
                }
              >
                {PRIORITIES.map((value) => (
                  <option key={value} value={value}>
                    {value[0].toUpperCase() + value.slice(1)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="text-xs text-slate-500">Destination contact</span>
              <input
                className="input"
                value={destinationContact}
                maxLength={320}
                onChange={(event) => setDestinationContact(event.target.value)}
              />
            </label>
            <label className="block">
              <span className="text-xs text-slate-500">Internal location</span>
              <input
                className="input"
                value={internalLocation}
                maxLength={320}
                placeholder="Building, floor, room"
                onChange={(event) => setInternalLocation(event.target.value)}
              />
            </label>
          </div>
          <label className="block">
            <span className="text-xs text-slate-500">
              Special handling instructions
            </span>
            <textarea
              className="input min-h-20"
              value={specialInstructions}
              maxLength={5000}
              onChange={(event) => setSpecialInstructions(event.target.value)}
            />
          </label>

          {direction === "inbound" ? (
            <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_12rem]">
              {warehouseId ? (
                <SuggestionPanel
                  loading={suggestion.isLoading}
                  suggestion={suggestion.data}
                />
              ) : (
                <div />
              )}
              <label className="block rounded-lg border border-slate-200 p-3">
                <span className="text-xs font-medium text-slate-700">
                  Requested quantity
                </span>
                <input
                  required
                  className="input mt-1"
                  type="number"
                  min={1}
                  max={5000}
                  value={quantity}
                  onChange={(event) => {
                    setQuantityTouched(true);
                    setQuantity(Number(event.target.value));
                  }}
                />
                <span className="mt-1 block text-xs text-slate-500">
                  Editable. The recommendation is guidance, not a mandatory value.
                </span>
              </label>
            </div>
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
                !!requestWarehouseValidationError(
                  direction,
                  targetWarehouseId,
                  availableWarehouses,
                ) ||
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
  suggestion,
}: {
  loading: boolean;
  suggestion:
    | RequestSuggestion
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
        <div>
          <h3 className="text-sm font-medium text-brand-900">Recommendation</h3>
          <span className="text-xs capitalize text-brand-700">
            {suggestion.confidence.replace("_", " ")} confidence
            {suggestion.fallback_used ? " · minimum-gap fallback" : ""}
          </span>
        </div>
        <strong className="text-lg tabular-nums text-brand-800">
          {suggestion.suggested_quantity} boxes
        </strong>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        <SuggestionValue label="Available" value={suggestion.current_available} />
        <SuggestionValue label="Baseline gap" value={suggestion.baseline_gap} />
        <SuggestionValue label="Pending inbound" value={suggestion.pending_inbound} />
        <SuggestionValue label="Backorders" value={suggestion.pending_backorder} />
        <SuggestionValue
          label="Lead-time demand"
          value={suggestion.lead_time_demand}
        />
        <SuggestionValue label="Safety stock" value={suggestion.safety_stock_quantity} />
        <SuggestionValue label="Adjustment" value={suggestion.adjustment_quantity} />
        <SuggestionValue label="Target inventory" value={suggestion.target_inventory} />
        <SuggestionValue label="Capacity limit" value={suggestion.capacity_limit} />
      </dl>
      <p className="mt-2 text-xs leading-5 text-slate-600">
        {suggestion.explanation}
      </p>
      {suggestion.fallback_reason && (
        <p className="mt-1 text-xs text-slate-500">
          Fallback reason: {suggestion.fallback_reason}
        </p>
      )}
      {suggestion.sample_size > 0 && (
        <p className="mt-1 text-xs text-slate-500">
          Observed {suggestion.history_30_quantity} qualifying transitions in 30
          days and {suggestion.history_90_quantity} in 90 days across{" "}
          {suggestion.history_days} history days. Weighted rate{" "}
          {suggestion.weighted_daily_rate.toFixed(2)} boxes/day using{" "}
          {Math.round(suggestion.normalized_30_weight * 100)}/
          {Math.round(suggestion.normalized_90_weight * 100)} weighting.
        </p>
      )}
      {(suggestion.scheduled_inbound > 0 ||
        suggestion.scheduled_return > 0) && (
        <p className="mt-1 text-xs text-slate-500">
          Within lead time: {suggestion.scheduled_inbound} inbound and{" "}
          {suggestion.scheduled_return} scheduled returns.
        </p>
      )}
      {suggestion.capacity_cap_applied && (
        <p className="mt-1 text-xs font-medium text-amber-700">
          The recommendation was reduced to stay within projected capacity.
        </p>
      )}
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

