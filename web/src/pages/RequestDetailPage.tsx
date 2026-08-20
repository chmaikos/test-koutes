import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  AlertTriangle,
  CalendarClock,
  Check,
  Download,
  FileText,
  FileSpreadsheet,
  PackageCheck,
  Pause,
  Play,
  Plus,
  Trash2,
  Upload,
  RotateCcw,
  X,
} from "lucide-react";
import {
  useDownloadRequestDocument,
  useDownloadDiscrepancyPhoto,
  useInboundCompletionPreview,
  useMe,
  useRequest,
  useRequestAction,
  useRequestDocuments,
  useRequestEvents,
  useReturnCandidates,
  useSubmitFollowUpDraft,
  useUploadRequestDocument,
  useUploadDiscrepancyPhoto,
  useWarehouses,
} from "@/api/hooks";
import type {
  BoxRequest,
  InboundCompletionPreview,
  InboundCompletionPreviewRow,
  InboundRequestItemInput,
  ReviewedInboundCompletionFields,
  RequestConflict,
  RequestDiscrepancyInput,
  RequestDocument,
  RequestDocumentType,
} from "@/api/types";
import { ExcelRowMapper } from "@/components/ExcelRowMapper";
import { LotPicker, type LotSelection } from "@/components/LotPicker";
import { PalletPicker, type PalletSelection } from "@/components/PalletPicker";
import {
  REQUEST_DIRECTION_LABEL,
  RequestStatusBadge,
} from "@/components/RequestStatusBadge";
import { requestPermissions } from "@/pages/requestPermissions";
import { normalizePalletNumber } from "@/pages/pallets";
import {
  RequestCoordinationPanel,
  RequestDiscussionPanel,
} from "@/components/RequestCoordination";
import {
  canRetryRequestConflict,
  conflictDetail,
} from "@/pages/requestConflict";
import {
  deliveryVariance,
  hasRequiredDiscrepancyReason,
} from "@/pages/xlsxMapping";
import { requestWarehouseRoute } from "@/pages/requestWarehouses";
import {
  EMPTY_INBOUND_COMPLETION_REVIEW,
  canSubmitInboundCompletion,
  currentInboundCompletionPreview,
  inboundBlockedMessage,
  inboundCompletionCounts,
  inboundCompletionFields,
  inboundCompletionFingerprint,
  inboundCompletionItems,
  inboundTargetPalletLabel,
  invalidateInboundCompletion,
  isStaleInboundImpactConflict,
  reviewedInboundCompletion,
  setInboundRelocationAcceptance,
} from "@/pages/inboundCompletion";

type ReasonDialog = "reject" | "cancel" | null;
type OperationalDialog =
  | "hold"
  | "resume"
  | "reschedule"
  | "failed"
  | "retry"
  | null;

export function RequestDetailPage() {
  const { id } = useParams<{ id: string }>();
  const requestId = id && /^\d+$/.test(id) ? Number(id) : undefined;
  const requestQuery = useRequest(requestId);
  const events = useRequestEvents(requestId);
  const documents = useRequestDocuments(requestId);
  const warehouses = useWarehouses(true);
  const me = useMe();
  const action = useRequestAction();
  const [reasonDialog, setReasonDialog] = useState<ReasonDialog>(null);
  const [operationalDialog, setOperationalDialog] =
    useState<OperationalDialog>(null);
  const [showCompletion, setShowCompletion] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{
    input: Parameters<typeof action.mutateAsync>[0];
    detail: RequestConflict;
  } | null>(null);

  if (requestQuery.isLoading) {
    return <p className="text-sm text-slate-500">Loading…</p>;
  }
  if (!requestQuery.data || requestQuery.error) {
    return (
      <div className="space-y-3">
        <BackLink />
        <p className="text-sm text-rose-600">Request not found.</p>
      </div>
    );
  }

  const request = requestQuery.data;
  const warehouseRoute = requestWarehouseRoute(
    request,
    warehouses.data ?? [],
  );
  const warehouseName = warehouseRoute.sourceName;
  const targetWarehouseName =
    warehouseRoute.targetName ?? warehouseRoute.sourceName;
  const {
    canMove,
    canApprove,
    canUpload,
    canCancel,
    canComplete,
    canPrepare,
    canMarkReady,
    canStartTransit,
    canMarkArrived,
    canHold,
    canResume,
    canReschedule,
    canReportFailed,
    canRetry,
  } = requestPermissions(request, me.data);
  const visibleDocuments = documents.data ?? request.documents;
  const requiredDocumentType: RequestDocumentType =
    request.direction === "inbound" ? "delivery_note" : "return_note";
  const hasRequiredDocument = visibleDocuments.some(
    (document) =>
      document.is_current &&
      document.document_type === requiredDocumentType,
  );

  async function perform(
    input: Parameters<typeof action.mutateAsync>[0],
    onStaleInboundImpact?: () => void,
  ): Promise<boolean> {
    setActionError(null);
    try {
      await action.mutateAsync(input);
      setConflict(null);
      return true;
    } catch (caught) {
      if (onStaleInboundImpact && isStaleInboundImpactConflict(caught)) {
        setConflict(null);
        setActionError(null);
        onStaleInboundImpact();
        return false;
      }
      const detail = conflictDetail(caught);
      if (detail) {
        setConflict({ input, detail });
        await requestQuery.refetch();
      }
      setActionError(apiError(caught, "The request could not be updated."));
      return false;
    }
  }

  return (
    <div className="space-y-5">
      <BackLink />

      <header className="card card-pad space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              Request #{request.id}
            </h1>
            {request.direction === "return" ? (
              <div className="mt-1">
                <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
                  Return route
                </p>
                <p className="text-lg font-semibold text-slate-800">
                  {warehouseRoute.label}
                </p>
              </div>
            ) : (
              <p className="mt-0.5 text-sm text-slate-500">
                {REQUEST_DIRECTION_LABEL[request.direction]} · Warehouse:{" "}
                {warehouseName}
              </p>
            )}
            {request.direction === "return" &&
              request.source_inbound_request_id !== null && (
                <Link
                  to={`/requests/${request.source_inbound_request_id}`}
                  className="mt-1 inline-block text-sm text-brand-700 hover:underline"
                >
                  Reverses inbound order #{request.source_inbound_request_id}
                </Link>
              )}
            {request.parent_request_id !== null && (
              <Link
                to={`/requests/${request.parent_request_id}`}
                className="mt-1 block text-sm text-brand-700 hover:underline"
              >
                Follow-up to request #{request.parent_request_id}
              </Link>
            )}
            {request.child_request_ids.map((childId) => (
              <Link
                key={childId}
                to={`/requests/${childId}`}
                className="mt-1 block text-sm text-brand-700 hover:underline"
              >
                Generated follow-up #{childId}
              </Link>
            ))}
          </div>
          <RequestStatusBadge
            status={request.status}
            direction={request.direction}
          />
        </div>

        <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Ordered quantity">{request.quantity} boxes</Field>
          {request.actual_received_quantity !== null && (
            <Field label="Actually received">
              {request.actual_received_quantity} boxes
            </Field>
          )}
          <Field label="Requested by">
            {request.requester_name ?? `User #${request.requester_user_id}`}
          </Field>
          <Field label="Priority">
            {humanize(request.priority)}
          </Field>
          <Field label="Assigned mover">
            {request.assigned_mover_name ?? "Unassigned"}
          </Field>
        </dl>

        <InventorySnapshot request={request} />

        {["xlsx_import", "manual_entry"].includes(request.origin) &&
          request.status === "submitted" && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              <strong>Pending self-receipt review.</strong> The mapped items are
              staged only; no physical boxes or capacity changes exist until an
              administrator finalizes this receipt.
              {request.receipt_document_required && !hasRequiredDocument && (
                <span className="block">
                  Upload a current ERP delivery note before finalization.
                </span>
              )}
            </div>
          )}

        {(request.rejection_reason || request.cancellation_reason) && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
            <strong>
              {request.rejection_reason ? "Rejection reason:" : "Cancellation reason:"}
            </strong>{" "}
            {request.rejection_reason ?? request.cancellation_reason}
          </div>
        )}
        {request.current_exception && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
            <strong>
              {request.current_exception.exception_kind === "hold"
                ? "Operational hold"
                : "Failed transport"}
            </strong>
            <span className="block">{request.current_exception.reason}</span>
            <span className="mt-1 block text-xs">
              Resume target: {humanize(request.current_exception.resume_target)}
              {" · "}
              opened {formatDate(request.current_exception.created_at)}
            </span>
          </div>
        )}
        {request.variance_quantity !== null &&
          request.variance_quantity !== 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              <strong>
                Delivery discrepancy:{" "}
                {request.variance_quantity < 0
                  ? `short by ${Math.abs(request.variance_quantity)}`
                  : `over by ${request.variance_quantity}`}{" "}
                boxes.
              </strong>{" "}
              {request.discrepancy_reason}
            </div>
          )}

        {(canMove || canCancel || canComplete || canResume || canRetry) && (
          <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-3">
            {canApprove && (
              <>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={
                    action.isPending ||
                    (request.receipt_document_required && !hasRequiredDocument)
                  }
                  onClick={() =>
                    void perform({
                      id: request.id,
                      expectedVersion: request.version,
                      action: "approve",
                    })
                  }
                >
                  <Check className="h-4 w-4" />{" "}
                  {["xlsx_import", "manual_entry"].includes(request.origin)
                    ? "Finalize receipt"
                    : "Approve"}
                </button>
                <button
                  type="button"
                  className="btn-danger"
                  disabled={action.isPending}
                  onClick={() => {
                    setActionError(null);
                    setReasonDialog("reject");
                  }}
                >
                  <X className="h-4 w-4" /> Reject
                </button>
              </>
            )}
            {canPrepare && (
              <button
                type="button"
                className="btn-primary"
                disabled={action.isPending}
                onClick={() =>
                  void perform({
                    id: request.id,
                    expectedVersion: request.version,
                    action: "prepare",
                  })
                }
              >
                <Play className="h-4 w-4" />{" "}
                {request.direction === "inbound"
                  ? "Prepare inbound"
                  : "Prepare return"}
              </button>
            )}
            {canMarkReady && (
              <button
                type="button"
                className="btn-primary"
                disabled={action.isPending}
                onClick={() =>
                  void perform({
                    id: request.id,
                    expectedVersion: request.version,
                    action: "mark-ready",
                  })
                }
              >
                <PackageCheck className="h-4 w-4" />{" "}
                {request.direction === "inbound"
                  ? "Ready for dispatch"
                  : "Ready for collection"}
              </button>
            )}
            {canStartTransit && (
              <button
                type="button"
                className="btn-primary"
                disabled={action.isPending || !hasRequiredDocument}
                title={
                  hasRequiredDocument
                    ? undefined
                    : `Upload a current ${documentTypeLabel(requiredDocumentType).toLowerCase()} first`
                }
                onClick={() =>
                  void perform({
                    id: request.id,
                    expectedVersion: request.version,
                    action: "start-transit",
                  })
                }
              >
                <Play className="h-4 w-4" />{" "}
                {request.direction === "inbound"
                  ? "Mark as delivering"
                  : "Start collection"}
              </button>
            )}
            {canMarkArrived && (
              <button
                type="button"
                className="btn-primary"
                disabled={action.isPending}
                onClick={() =>
                  void perform({
                    id: request.id,
                    expectedVersion: request.version,
                    action: "mark-arrived",
                  })
                }
              >
                <PackageCheck className="h-4 w-4" />{" "}
                {request.direction === "inbound"
                  ? "Mark delivered"
                  : "Mark collected"}
              </button>
            )}
            {canComplete && (
              <button
                type="button"
                className="btn-primary"
                disabled={action.isPending}
                onClick={() => {
                  setActionError(null);
                  setShowCompletion(true);
                }}
              >
                <PackageCheck className="h-4 w-4" />{" "}
                {request.direction === "inbound"
                  ? "Confirm delivery"
                  : "Confirm warehouse receipt"}
              </button>
            )}
            {canHold && (
              <button
                type="button"
                className="btn-secondary"
                disabled={action.isPending}
                onClick={() => setOperationalDialog("hold")}
              >
                <Pause className="h-4 w-4" /> Hold
              </button>
            )}
            {canResume && (
              <button
                type="button"
                className="btn-secondary"
                disabled={action.isPending}
                onClick={() => setOperationalDialog("resume")}
              >
                <Play className="h-4 w-4" /> Resume
              </button>
            )}
            {canReschedule && (
              <button
                type="button"
                className="btn-secondary"
                disabled={action.isPending}
                onClick={() => setOperationalDialog("reschedule")}
              >
                <CalendarClock className="h-4 w-4" /> Reschedule
              </button>
            )}
            {canReportFailed && (
              <button
                type="button"
                className="btn-secondary text-rose-700"
                disabled={action.isPending}
                onClick={() => setOperationalDialog("failed")}
              >
                <AlertTriangle className="h-4 w-4" /> Report failed transport
              </button>
            )}
            {canRetry && (
              <button
                type="button"
                className="btn-primary"
                disabled={action.isPending}
                onClick={() => setOperationalDialog("retry")}
              >
                <RotateCcw className="h-4 w-4" /> Retry transport
              </button>
            )}
            {canCancel && (
              <button
                type="button"
                className="btn-secondary text-rose-700"
                disabled={action.isPending}
                onClick={() => {
                  setActionError(null);
                  setReasonDialog("cancel");
                }}
              >
                Cancel request
              </button>
            )}
          </div>
        )}
        {actionError && (
          <p role="alert" className="text-sm text-rose-600">
            {actionError}
          </p>
        )}
        {conflict && (
          <VersionConflict
            detail={conflict.detail}
            current={request}
            pending={action.isPending}
            onRetry={() =>
              void perform({
                ...conflict.input,
                expectedVersion: request.version,
              })
            }
          />
        )}
      </header>

      {request.status === "draft" && (
        <DraftSubmission request={request} />
      )}

      <RequestCoordinationPanel request={request} />

      {request.items.length > 0 && (
        <ItemsSection
          request={request}
          warehouseName={warehouseName}
          targetWarehouseName={targetWarehouseName}
        />
      )}
      {request.discrepancies.length > 0 && (
        <DiscrepanciesSection request={request} />
      )}

      <RequestDiscussionPanel request={request} />

      <div className="grid gap-5 lg:grid-cols-2">
        <Timeline
          events={events.data ?? []}
          loading={events.isLoading}
          direction={request.direction}
        />
        <DocumentsSection
          requestId={request.id}
          direction={request.direction}
          documents={visibleDocuments}
          loading={documents.isLoading}
          canUpload={canUpload}
          requestVersion={request.version}
          requestStatus={request.status}
        />
      </div>

      {reasonDialog && (
        <ReasonActionDialog
          kind={reasonDialog}
          pending={action.isPending}
          error={actionError}
          onClose={() => setReasonDialog(null)}
          onSubmit={async (reason) => {
            const ok =
              reasonDialog === "reject"
                ? await perform({
                    id: request.id,
                    expectedVersion: request.version,
                    action: "reject",
                    body: { reason },
                  })
                : await perform({
                    id: request.id,
                    expectedVersion: request.version,
                    action: "cancel",
                    body: { reason: reason || undefined },
                  });
            if (ok) setReasonDialog(null);
          }}
        />
      )}
      {operationalDialog && (
        <OperationalExceptionDialog
          kind={operationalDialog}
          request={request}
          pending={action.isPending}
          error={actionError}
          onClose={() => setOperationalDialog(null)}
          onSubmit={async ({ reason, windowStart, windowEnd }) => {
            const base = {
              id: request.id,
              expectedVersion: request.version,
            };
            const ok =
              operationalDialog === "hold"
                ? await perform({
                    ...base,
                    action: "hold",
                    body: { reason },
                  })
                : operationalDialog === "resume"
                  ? await perform({
                      ...base,
                      action: "resume",
                      body: { resolution: reason },
                    })
                  : operationalDialog === "retry"
                    ? await perform({
                        ...base,
                        action: "retry-transport",
                        body: { resolution: reason },
                      })
                    : operationalDialog === "reschedule"
                      ? await perform({
                          ...base,
                          action: "reschedule",
                          body: {
                            reason,
                            revised_window_start: new Date(
                              windowStart,
                            ).toISOString(),
                            revised_window_end: new Date(windowEnd).toISOString(),
                          },
                        })
                      : await perform({
                          ...base,
                          action: "report-failed-delivery",
                          body: {
                            reason,
                            ...(windowStart && windowEnd
                              ? {
                                  revised_window_start: new Date(
                                    windowStart,
                                  ).toISOString(),
                                  revised_window_end: new Date(
                                    windowEnd,
                                  ).toISOString(),
                                }
                              : {}),
                          },
                        });
            if (ok) setOperationalDialog(null);
          }}
        />
      )}
      {showCompletion && (
        <CompletionDialog
          request={request}
          sourceWarehouseName={warehouseName}
          targetWarehouseName={targetWarehouseName}
          pending={action.isPending}
          error={actionError}
          onClose={() => setShowCompletion(false)}
          onSubmit={async (
            items,
            discrepancyReason,
            collectedBoxIds,
            discrepancies,
            completionFields,
          ) => {
            let staleInboundImpact = false;
            const ok = await perform({
              id: request.id,
              expectedVersion: request.version,
              action: "complete",
              body:
                request.direction === "inbound"
                  ? {
                      inbound_items: items,
                      discrepancy_reason: discrepancyReason || undefined,
                      discrepancies,
                      ...completionFields,
                      idempotency_key: crypto.randomUUID(),
                    }
                  : {
                      collected_box_ids: collectedBoxIds,
                      discrepancies,
                      discrepancy_reason: discrepancyReason || undefined,
                      idempotency_key: crypto.randomUUID(),
                    },
            }, () => {
              staleInboundImpact = true;
            });
            if (ok) setShowCompletion(false);
            return ok
              ? "completed"
              : staleInboundImpact
                ? "stale"
                : "failed";
          }}
        />
      )}
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/requests"
      className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
    >
      <ArrowLeft className="h-4 w-4" /> All requests
    </Link>
  );
}

function InventorySnapshot({ request }: { request: BoxRequest }) {
  return (
    <section className="rounded-lg bg-slate-50 p-3">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        Inventory at submission
      </h2>
      <dl className="mt-2 grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
        <Field label="Available">{request.current_available}</Field>
        <Field label="Minimum">{request.min_inventory}</Field>
        <Field label="Pending inbound">{request.pending_inbound}</Field>
        <Field label="Return eligible">{request.eligible_return}</Field>
        <Field label="Suggested">{request.suggestion_quantity}</Field>
      </dl>
    </section>
  );
}

function ItemsSection({
  request,
  warehouseName,
  targetWarehouseName,
}: {
  request: BoxRequest;
  warehouseName: string;
  targetWarehouseName: string;
}) {
  return (
    <section className="card overflow-hidden">
      <header className="border-b border-slate-100 px-5 py-3">
        <h2 className="font-semibold">Boxes</h2>
        <p className="text-xs text-slate-500">
          {request.direction === "inbound"
            ? `Boxes received at ${warehouseName}.`
            : `Returned boxes are moved from ${warehouseName} to ${targetWarehouseName} when this request is completed.`}
        </p>
      </header>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500">
            <tr>
              <th className="px-4 py-2.5">Box #</th>
              <th className="px-4 py-2.5">Lot</th>
              <th className="px-4 py-2.5">Pallet</th>
              <th className="px-4 py-2.5">Item descriptions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {request.items.map((item) => (
              <tr key={item.id}>
                <td className="px-4 py-2.5 font-mono">
                  {item.box_id ? (
                    <Link
                      to={`/boxes/${item.box_id}`}
                      className="text-brand-700 hover:underline"
                    >
                      {item.box_number ?? `#${item.box_id}`}
                    </Link>
                  ) : (
                    item.box_number ?? "—"
                  )}
                </td>
                <td className="px-4 py-2.5">
                  {item.lot_id ? (
                    <Link className="text-brand-700 hover:underline" to={`/lots/${item.lot_id}`}>
                      {item.lot ?? `Lot #${item.lot_id}`}
                    </Link>
                  ) : (
                    item.lot ?? "—"
                  )}
                </td>
                <td className="px-4 py-2.5">
                  {item.pallet_id ? (
                    <Link className="text-brand-700 hover:underline" to={`/pallets/${item.pallet_id}`}>
                      {item.pallet_number ?? item.pallet ?? `#${item.pallet_id}`}
                    </Link>
                  ) : (
                    <span className="text-amber-700">Unassigned</span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-slate-600">
                  {item.contents ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function DraftSubmission({ request }: { request: BoxRequest }) {
  const candidates = useReturnCandidates(
    request.source_inbound_request_id ?? undefined,
  );
  const submit = useSubmitFollowUpDraft();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  return (
    <section className="card card-pad space-y-3">
      <h2 className="font-semibold">Reselect boxes for this follow-up</h2>
      <p className="text-sm text-slate-600">
        This draft does not reserve inventory. Select exactly {request.quantity}{" "}
        currently eligible box(es) to submit it.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {(candidates.data ?? []).map((box) => (
          <label
            key={box.box_id}
            className="flex items-center gap-2 rounded border border-slate-200 p-3"
          >
            <input
              type="checkbox"
              checked={selected.has(box.box_id)}
              onChange={() =>
                setSelected((current) => {
                  const next = new Set(current);
                  if (next.has(box.box_id)) next.delete(box.box_id);
                  else next.add(box.box_id);
                  return next;
                })
              }
            />
            <span className="font-mono">{box.box_number}</span>
            <Link
              to={`/lots/${box.lot_id}`}
              className="text-xs text-brand-700 hover:underline"
            >
              {box.lot}
            </Link>
            <span className="text-xs text-slate-500">
              {box.pallet_number ?? "Unassigned"}
            </span>
          </label>
        ))}
      </div>
      {error && <p className="text-sm text-rose-600">{error}</p>}
      <button
        type="button"
        className="btn-primary"
        disabled={selected.size !== request.quantity || submit.isPending}
        onClick={async () => {
          setError(null);
          try {
            await submit.mutateAsync({
              requestId: request.id,
              expectedVersion: request.version,
              boxIds: [...selected],
            });
          } catch (caught) {
            setError(apiError(caught, "The draft could not be submitted."));
          }
        }}
      >
        Submit follow-up
      </button>
    </section>
  );
}

function DiscrepanciesSection({ request }: { request: BoxRequest }) {
  const upload = useUploadDiscrepancyPhoto();
  const download = useDownloadDiscrepancyPhoto();
  const [files, setFiles] = useState<Record<number, File | undefined>>({});
  const [error, setError] = useState<string | null>(null);
  return (
    <section className="card overflow-hidden">
      <header className="border-b border-slate-100 px-5 py-3">
        <h2 className="font-semibold">Fulfillment discrepancies</h2>
      </header>
      <div className="divide-y divide-slate-100">
        {request.discrepancies.map((item) => (
          <div key={item.id} className="space-y-2 px-5 py-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="badge bg-amber-100 text-amber-800">
                {humanize(item.discrepancy_type)}
              </span>
              {item.quantity && <span>Quantity: {item.quantity}</span>}
              {item.box_id && (
                <Link to={`/boxes/${item.box_id}`} className="text-brand-700">
                  Box #{item.box_id}
                </Link>
              )}
            </div>
            {item.notes && <p className="text-slate-600">{item.notes}</p>}
            {item.photos.map((photo) => (
              <button
                key={photo.id}
                type="button"
                className="btn-ghost text-xs"
                disabled={download.isPending}
                onClick={() =>
                  download.mutate({
                    requestId: request.id,
                    discrepancyId: item.id,
                    photoId: photo.id,
                    filename: photo.original_filename,
                  })
                }
              >
                <Download className="h-3.5 w-3.5" />
                {photo.original_filename}
              </button>
            ))}
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="file"
                accept="image/jpeg,image/png"
                className="input max-w-sm"
                onChange={(event) =>
                  setFiles((current) => ({
                    ...current,
                    [item.id]: event.target.files?.[0],
                  }))
                }
              />
              <button
                type="button"
                className="btn-secondary"
                disabled={!files[item.id] || upload.isPending}
                onClick={async () => {
                  const file = files[item.id];
                  if (!file) return;
                  setError(null);
                  try {
                    await upload.mutateAsync({
                      requestId: request.id,
                      discrepancyId: item.id,
                      expectedVersion: request.version,
                      file,
                    });
                    setFiles((current) => ({ ...current, [item.id]: undefined }));
                  } catch (caught) {
                    setError(apiError(caught, "Photo upload failed."));
                  }
                }}
              >
                Upload photo
              </button>
            </div>
          </div>
        ))}
      </div>
      {error && <p className="px-5 py-3 text-sm text-rose-600">{error}</p>}
    </section>
  );
}

function Timeline({
  events,
  loading,
  direction,
}: {
  events: import("@/api/types").RequestEvent[];
  loading: boolean;
  direction: BoxRequest["direction"];
}) {
  return (
    <section className="card card-pad">
      <h2 className="font-semibold">Timeline</h2>
      {loading ? (
        <p className="mt-3 text-sm text-slate-400">Loading activity…</p>
      ) : events.length === 0 ? (
        <p className="mt-3 text-sm text-slate-400">No activity recorded.</p>
      ) : (
        <ol className="mt-4 space-y-0">
          {events.map((event, index) => (
            <li key={event.id} className="relative flex gap-3 pb-5 last:pb-0">
              {index < events.length - 1 && (
                <div className="absolute left-[5px] top-3 h-full w-px bg-slate-200" />
              )}
              <div className="relative mt-1 h-3 w-3 flex-none rounded-full border-2 border-white bg-brand-500 ring-1 ring-brand-200" />
              <div className="min-w-0">
                <p className="text-sm font-medium text-slate-800">
                  {eventLabel(event, direction)}
                </p>
                <p className="text-xs text-slate-500">
                  {formatDate(event.occurred_at)}
                  {event.user_name ? ` · ${event.user_name}` : ""}
                </p>
                {event.note && (
                  <p className="mt-1 break-words text-xs text-slate-600">
                    {event.note}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function DocumentsSection({
  requestId,
  direction,
  documents,
  loading,
  canUpload,
  requestVersion,
  requestStatus,
}: {
  requestId: number;
  direction: BoxRequest["direction"];
  documents: RequestDocument[];
  loading: boolean;
  canUpload: boolean;
  requestVersion: number;
  requestStatus: BoxRequest["status"];
}) {
  const upload = useUploadRequestDocument();
  const download = useDownloadRequestDocument();
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);
  const [documentType, setDocumentType] =
    useState<RequestDocumentType>(
      direction === "inbound" ? "delivery_note" : "return_note",
    );
  const [erpReference, setErpReference] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setError(null);
    try {
      await upload.mutateAsync({
        requestId,
        file,
        documentType,
        erpReference: erpReference.trim(),
        expectedVersion: requestVersion,
      });
      setFile(null);
      setFileKey((value) => value + 1);
      setErpReference("");
    } catch (caught) {
      setError(apiError(caught, "Document upload failed."));
    }
  }

  return (
    <section className="card overflow-hidden">
      <header className="border-b border-slate-100 px-5 py-3">
        <h2 className="font-semibold">ERP documents</h2>
        <p className="text-xs text-slate-500">
          Delivery notes, return notes, and supporting files.
        </p>
      </header>
      <div className="divide-y divide-slate-100">
        {loading && (
          <p className="px-5 py-4 text-sm text-slate-400">Loading documents…</p>
        )}
        {!loading && documents.length === 0 && (
          <p className="px-5 py-4 text-sm text-slate-400">
            No documents uploaded.
          </p>
        )}
        {documents.map((document) => (
          <div key={document.id} className="flex items-center gap-3 px-5 py-3">
            <FileText className="h-5 w-5 flex-none text-slate-400" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="truncate text-sm font-medium">
                  {document.original_filename}
                </span>
                {document.is_current && (
                  <span className="badge bg-emerald-100 text-emerald-700">
                    Current
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-500">
                {documentTypeLabel(document.document_type)}
                {document.erp_reference
                  ? ` · ERP ${document.erp_reference}`
                  : ""}
                {" · "}
                {formatBytes(document.size_bytes)}
                {" · "}
                {formatDate(document.created_at)}
              </p>
            </div>
            <button
              type="button"
              className="btn-ghost"
              aria-label={`Download ${document.original_filename}`}
              disabled={download.isPending}
              onClick={() =>
                download.mutate({
                  requestId,
                  documentId: document.id,
                  filename: document.original_filename,
                })
              }
            >
              <Download className="h-4 w-4" />
              <span className="hidden sm:inline">Download</span>
            </button>
          </div>
        ))}
      </div>

      {canUpload && (
        <form className="space-y-3 border-t border-slate-100 p-5" onSubmit={submit}>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="text-xs text-slate-500">Document type</span>
              <select
                className="input"
                value={documentType}
                onChange={(event) =>
                  setDocumentType(event.target.value as RequestDocumentType)
                }
              >
                <option value="delivery_note">Delivery note</option>
                <option value="return_note">Return note</option>
                <option value="other">Other</option>
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-slate-500">
                ERP reference
              </span>
              <input
                required
                className="input"
                value={erpReference}
                onChange={(event) => setErpReference(event.target.value)}
                maxLength={120}
              />
            </label>
          </div>
          <label className="block">
            <span className="text-xs text-slate-500">File</span>
            <input
              key={fileKey}
              required
              type="file"
              className="input file:mr-3 file:border-0 file:bg-transparent file:text-sm"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>
          {error && (
            <div role="alert" className="rounded border border-rose-200 bg-rose-50 p-2 text-sm text-rose-700">
              {error}
              {conflictDetail(upload.error) && (
                <p className="mt-1 text-xs">
                  Latest server state: {humanize(conflictDetail(upload.error)!.latest_status)}
                  {" · "}version {conflictDetail(upload.error)!.latest_version}. Your file and fields were preserved; review the refreshed request and submit again only if it is still {humanize(requestStatus)}.
                </p>
              )}
            </div>
          )}
          <button
            type="submit"
            className="btn-secondary"
            disabled={!file || !erpReference.trim() || upload.isPending}
          >
            <Upload className="h-4 w-4" />
            {upload.isPending ? "Uploading…" : "Upload document"}
          </button>
        </form>
      )}
    </section>
  );
}

function OperationalExceptionDialog({
  kind,
  request,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  kind: Exclude<OperationalDialog, null>;
  request: BoxRequest;
  pending: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (value: {
    reason: string;
    windowStart: string;
    windowEnd: string;
  }) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [windowStart, setWindowStart] = useState(
    localDateInput(request.scheduled_window_start),
  );
  const [windowEnd, setWindowEnd] = useState(
    localDateInput(request.scheduled_window_end),
  );
  const needsWindow = kind === "reschedule";
  const maySetWindow = needsWindow || kind === "failed";
  const title = {
    hold: "Place request on hold",
    resume: "Resume request",
    reschedule: "Reschedule transport",
    failed: "Report failed transport",
    retry: "Retry transport",
  }[kind];
  const reasonLabel =
    kind === "resume" || kind === "retry" ? "Resolution" : "Reason";
  const windowValid =
    !needsWindow ||
    (!!windowStart &&
      !!windowEnd &&
      new Date(windowEnd).getTime() > new Date(windowStart).getTime());
  const optionalWindowValid =
    kind !== "failed" ||
    ((!windowStart && !windowEnd) ||
      (!!windowStart &&
        !!windowEnd &&
        new Date(windowEnd).getTime() > new Date(windowStart).getTime()));

  return (
    <div className="modal-backdrop">
      <div className="modal-sheet max-w-lg" role="dialog" aria-modal="true">
        <h2 className="text-lg font-semibold">{title}</h2>
        <form
          className="mt-4 space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (windowValid && optionalWindowValid) {
              void onSubmit({
                reason: reason.trim(),
                windowStart,
                windowEnd,
              });
            }
          }}
        >
          <label className="block">
            <span className="text-xs text-slate-500">{reasonLabel}</span>
            <textarea
              autoFocus
              required
              rows={3}
              maxLength={2000}
              className="input"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          {maySetWindow && (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="text-xs text-slate-500">
                  Revised window start {kind === "failed" ? "(optional)" : ""}
                </span>
                <input
                  type="datetime-local"
                  required={needsWindow}
                  className="input"
                  value={windowStart}
                  onChange={(event) => setWindowStart(event.target.value)}
                />
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">
                  Revised window end {kind === "failed" ? "(optional)" : ""}
                </span>
                <input
                  type="datetime-local"
                  required={needsWindow}
                  className="input"
                  value={windowEnd}
                  onChange={(event) => setWindowEnd(event.target.value)}
                />
              </label>
            </div>
          )}
          {(!windowValid || !optionalWindowValid) && (
            <p className="text-sm text-rose-600">
              Enter both window values with the end after the start.
            </p>
          )}
          {error && <p className="text-sm text-rose-600">{error}</p>}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              className="btn-secondary"
              disabled={pending}
              onClick={onClose}
            >
              Close
            </button>
            <button
              type="submit"
              className={kind === "failed" ? "btn-danger" : "btn-primary"}
              disabled={
                pending ||
                !reason.trim() ||
                !windowValid ||
                !optionalWindowValid
              }
            >
              {pending ? "Working…" : title}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ReasonActionDialog({
  kind,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  kind: Exclude<ReasonDialog, null>;
  pending: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const rejecting = kind === "reject";
  return (
    <div className="modal-backdrop">
      <div
        className="modal-sheet max-w-md"
        role="dialog"
        aria-modal="true"
        aria-labelledby="reason-dialog-title"
      >
        <h2 id="reason-dialog-title" className="text-lg font-semibold">
          {rejecting ? "Reject request" : "Cancel request"}
        </h2>
        <form
          className="mt-4 space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void onSubmit(reason.trim());
          }}
        >
          <label className="block">
            <span className="text-xs text-slate-500">
              Reason {rejecting ? "" : "(optional)"}
            </span>
            <textarea
              autoFocus
              required={rejecting}
              rows={3}
              maxLength={500}
              className="input"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          {error && (
            <p role="alert" className="text-sm text-rose-600">
              {error}
            </p>
          )}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              className="btn-secondary"
              disabled={pending}
              onClick={onClose}
            >
              Keep request
            </button>
            <button
              type="submit"
              className="btn-danger"
              disabled={pending || (rejecting && !reason.trim())}
            >
              {pending
                ? "Working…"
                : rejecting
                  ? "Reject request"
                  : "Cancel request"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function VersionConflict({
  detail,
  current,
  pending,
  onRetry,
}: {
  detail: RequestConflict;
  current: BoxRequest;
  pending: boolean;
  onRetry: () => void;
}) {
  const stillValid = canRetryRequestConflict(current, detail);
  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
      <strong>This request changed while you were editing.</strong>
      <p className="mt-1">
        Server: {humanize(detail.latest_status)}, version {detail.latest_version}.
        Your open form and unsaved values were preserved.
      </p>
      {detail.relevant_events.length > 0 && (
        <p className="mt-1 text-xs">
          Latest activity: {eventLabel(detail.relevant_events[0])}
          {" · "}
          {formatDate(detail.relevant_events[0].occurred_at)}
        </p>
      )}
      {stillValid ? (
        <button
          type="button"
          className="btn-secondary mt-2"
          disabled={pending}
          onClick={onRetry}
        >
          Retry against version {current.version}
        </button>
      ) : (
        <p className="mt-2 text-xs font-medium">
          The action is no longer valid in the latest status. Review the request
          before continuing.
        </p>
      )}
    </div>
  );
}

function DiscrepancyEditor({
  value,
  onChange,
  items = [],
}: {
  value: RequestDiscrepancyInput[];
  onChange: (value: RequestDiscrepancyInput[]) => void;
  items?: BoxRequest["items"];
}) {
  return (
    <div className="space-y-2 rounded-lg border border-slate-200 p-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium">Line discrepancies</h3>
          <p className="text-xs text-slate-500">
            Record damage, wrong lot/item descriptions, rejected, missing, or unexpected boxes.
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary"
          onClick={() =>
            onChange([
              ...value,
              { discrepancy_type: "damaged", quantity: 1, notes: "" },
            ])
          }
        >
          <Plus className="h-4 w-4" /> Add
        </button>
      </div>
      {value.map((entry, index) => (
        <div key={index} className="grid gap-2 sm:grid-cols-4">
          <select
            className="input"
            value={entry.discrepancy_type}
            onChange={(event) =>
              onChange(
                value.map((candidate, candidateIndex) =>
                  candidateIndex === index
                    ? {
                        ...candidate,
                        discrepancy_type: event.target
                          .value as RequestDiscrepancyInput["discrepancy_type"],
                      }
                    : candidate,
                ),
              )
            }
          >
            {["missing", "unexpected", "damaged", "wrong_lot", "wrong_contents", "rejected"].map(
              (type) => (
                <option key={type} value={type}>
                  {type === "wrong_contents"
                    ? "Wrong item descriptions"
                    : humanize(type)}
                </option>
              ),
            )}
          </select>
          <select
            className="input"
            value={entry.request_item_id ?? ""}
            onChange={(event) => {
              const item = items.find(
                (candidate) => candidate.id === Number(event.target.value),
              );
              onChange(
                value.map((candidate, candidateIndex) =>
                  candidateIndex === index
                    ? {
                        ...candidate,
                        request_item_id: item?.id,
                        box_id: item?.box_id ?? undefined,
                      }
                    : candidate,
                ),
              );
            }}
          >
            <option value="">Aggregate / no box</option>
            {items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.box_number ?? `Item ${item.id}`}
              </option>
            ))}
          </select>
          <input
            type="number"
            min={1}
            max={5000}
            className="input"
            value={entry.quantity ?? ""}
            placeholder="Quantity"
            onChange={(event) =>
              onChange(
                value.map((candidate, candidateIndex) =>
                  candidateIndex === index
                    ? {
                        ...candidate,
                        quantity: event.target.value
                          ? Number(event.target.value)
                          : undefined,
                      }
                    : candidate,
                ),
              )
            }
          />
          <div className="flex gap-2">
            <input
              className="input"
              value={entry.notes ?? ""}
              placeholder="Notes"
              onChange={(event) =>
                onChange(
                  value.map((candidate, candidateIndex) =>
                    candidateIndex === index
                      ? { ...candidate, notes: event.target.value }
                      : candidate,
                  ),
                )
              }
            />
            <button
              type="button"
              className="btn-ghost text-rose-600"
              aria-label="Remove discrepancy"
              onClick={() =>
                onChange(value.filter((_, candidateIndex) => candidateIndex !== index))
              }
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function CompletionDialog({
  request,
  sourceWarehouseName,
  targetWarehouseName,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  request: BoxRequest;
  sourceWarehouseName: string;
  targetWarehouseName: string;
  pending: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (
    items: InboundRequestItemInput[],
    discrepancyReason?: string,
    collectedBoxIds?: number[],
    discrepancies?: RequestDiscrepancyInput[],
    completionFields?: ReviewedInboundCompletionFields,
  ) => Promise<"completed" | "stale" | "failed">;
}) {
  const inbound = request.direction === "inbound";
  const me = useMe();
  const previewMutation = useInboundCompletionPreview();
  const [rows, setRows] = useState<InboundRequestItemInput[]>(() =>
    inbound
      ? Array.from({ length: request.quantity }, () => ({
          lot: "",
          box_number: "",
          pallet_number: null,
          contents: "",
        }))
      : [],
  );
  const [showExcelMapper, setShowExcelMapper] = useState(false);
  const [rowLots, setRowLots] = useState<Record<number, LotSelection | null>>({});
  const [rowPallets, setRowPallets] = useState<Record<number, PalletSelection | null>>({});
  const [discrepancyReason, setDiscrepancyReason] = useState("");
  const [lineDiscrepancies, setLineDiscrepancies] = useState<
    RequestDiscrepancyInput[]
  >([]);
  const [impactReview, setImpactReview] = useState(
    EMPTY_INBOUND_COMPLETION_REVIEW,
  );
  const [collectedBoxIds, setCollectedBoxIds] = useState<Set<number>>(
    () =>
      new Set(
        request.items.flatMap((item) =>
          item.box_id === null ? [] : [item.box_id],
        ),
      ),
  );
  const groupedRows = inboundCompletionItems(rows, rowPallets);
  const actualCount = groupedRows.items.length;
  const variance = deliveryVariance(request.quantity, actualCount);
  const hasMismatch = variance !== 0;
  const allLotsConfirmed =
    !inbound ||
    rows.every(
      (row, index) =>
        !!rowLots[index] &&
        rowLots[index]?.name.trim().toLowerCase() ===
          row.lot.trim().replace(/\s+/g, " ").toLowerCase(),
    );
  const allPalletsConfirmed =
    !inbound ||
    rows.every(
      (row, index) =>
        !row.pallet_number?.trim() ||
        (!!rowPallets[index] &&
          normalizePalletNumber(rowPallets[index]?.pallet_number ?? "") ===
            normalizePalletNumber(row.pallet_number)),
    );
  const allRowsComplete =
    inbound &&
    rows.length > 0 &&
    rows.every(
      (row) =>
        row.box_number.trim().length > 0 &&
        row.lot.trim().length > 0,
    );
  const impactFingerprint = inboundCompletionFingerprint({
    requestId: request.id,
    requestVersion: request.version,
    rows,
    lotConfirmations: rowLots,
    palletConfirmations: rowPallets,
  });
  const currentImpactPreview = currentInboundCompletionPreview(
    impactReview,
    impactFingerprint,
    request.version,
  );
  const readyToReview =
    allRowsComplete &&
    allLotsConfirmed &&
    allPalletsConfirmed &&
    actualCount > 0 &&
    !groupedRows.error;
  const canCompleteImpact = canSubmitInboundCompletion(
    currentImpactPreview,
    impactReview.acceptRelocations,
  );

  useEffect(() => {
    setImpactReview(invalidateInboundCompletion());
    previewMutation.reset();
    // The fingerprint captures every row, confirmation, and request-version input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [impactFingerprint]);

  async function reviewInventoryImpact() {
    if (!readyToReview) return;
    try {
      const preview = await previewMutation.mutateAsync({
        id: request.id,
        payload: { inbound_items: groupedRows.items },
      });
      setImpactReview(reviewedInboundCompletion(preview, impactFingerprint));
    } catch {
      setImpactReview(invalidateInboundCompletion());
    }
  }

  function updateRow(
    index: number,
    field: keyof InboundRequestItemInput,
    value: string,
  ) {
    setRows((current) =>
      current.map((row, rowIndex) =>
        rowIndex === index ? { ...row, [field]: value } : row,
      ),
    );
  }

  return (
    <div className="modal-backdrop">
      <div
        className="modal-sheet max-h-[90vh] max-w-4xl overflow-y-auto"
        role="dialog"
        aria-modal="true"
        aria-labelledby="complete-dialog-title"
      >
        <h2 id="complete-dialog-title" className="text-lg font-semibold">
          Complete request #{request.id}
        </h2>
        {inbound ? (
          <form
            className="mt-3 space-y-4"
            onSubmit={async (event) => {
              event.preventDefault();
              if (!currentImpactPreview) {
                await reviewInventoryImpact();
                return;
              }
              if (!canCompleteImpact) return;
              const result = await onSubmit(
                groupedRows.items,
                hasMismatch ? discrepancyReason.trim() || undefined : undefined,
                undefined,
                lineDiscrepancies,
                inboundCompletionFields(
                  currentImpactPreview,
                  impactReview.acceptRelocations,
                ),
              );
              if (result === "stale") {
                setImpactReview(
                  invalidateInboundCompletion(
                    "Inventory changed; review again",
                  ),
                );
              }
            }}
          >
            <p className="text-sm text-slate-600">
              Enter the boxes that physically arrived. The ordered quantity is{" "}
              {request.quantity}; a different actual count is allowed but must
              include a discrepancy reason.
            </p>
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-medium text-slate-800">
                    Fill from an Excel workbook
                  </h3>
                  <p className="text-xs text-slate-500">
                    Upload any .xlsx layout, choose the sheet and columns, then
                    manually select the rows for this delivery.
                  </p>
                </div>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setShowExcelMapper((open) => !open)}
                >
                  <FileSpreadsheet className="h-4 w-4" />
                  {showExcelMapper ? "Close mapper" : "Use Excel"}
                </button>
              </div>
              {showExcelMapper && (
                <ExcelRowMapper
                  useCase="inbound_acceptance"
                  warehouseId={request.warehouse_id}
                  requestId={request.id}
                  quantity={request.quantity}
                  onApply={(mappedRows) => {
                    setRows(mappedRows);
                    setRowLots({});
                    setRowPallets({});
                    setShowExcelMapper(false);
                  }}
                />
              )}
            </div>
            <div className="space-y-3">
              {rows.map((row, index) => (
                <fieldset
                  key={index}
                  className="rounded-lg border border-slate-200 p-3"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-slate-500">
                      Box row {index + 1} of {rows.length}
                    </span>
                    <button
                      type="button"
                      className="btn-ghost text-rose-600"
                      disabled={rows.length <= 1}
                      onClick={() =>
                        setRows((current) => {
                          setRowLots({});
                          setRowPallets({});
                          return current.filter((_, rowIndex) => rowIndex !== index);
                        })
                      }
                    >
                      <Trash2 className="h-4 w-4" /> Remove
                    </button>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    <label className="block">
                      <span className="text-xs text-slate-500">Box number</span>
                      <input
                        required
                        className="input"
                        value={row.box_number}
                        onChange={(event) =>
                          updateRow(index, "box_number", event.target.value)
                        }
                      />
                    </label>
                    <LotPicker
                      value={rowLots[index] ?? null}
                      nameValue={row.lot}
                      onNameChange={(name) => updateRow(index, "lot", name)}
                      onChange={(selection) =>
                        setRowLots((current) => ({
                          ...current,
                          [index]: selection,
                        }))
                      }
                      warehouseId={request.warehouse_id}
                      canCreate={
                        me.data?.role === "admin" || me.data?.role === "operator"
                      }
                      label="Lot"
                    />
                    <PalletPicker
                      value={rowPallets[index] ?? null}
                      numberValue={row.pallet_number}
                      onNumberChange={(number) =>
                        updateRow(index, "pallet_number", number)
                      }
                      onChange={(selection) =>
                        setRowPallets((current) => ({
                          ...current,
                          [index]: selection,
                        }))
                      }
                      lotId={rowLots[index]?.id}
                      warehouseId={request.warehouse_id}
                      canCreate={
                        me.data?.role === "admin" || me.data?.role === "operator"
                      }
                      label="Pallet"
                    />
                    <label className="block">
                      <span className="text-xs text-slate-500">
                        Item descriptions (optional)
                      </span>
                      <input
                        className="input"
                        maxLength={2000}
                        value={row.contents ?? ""}
                        onChange={(event) =>
                          updateRow(index, "contents", event.target.value)
                        }
                      />
                    </label>
                  </div>
                </fieldset>
              ))}
              <button
                type="button"
                className="btn-secondary"
                onClick={() =>
                  setRows((current) => [
                    ...current,
                    {
                      lot: "",
                      box_number: "",
                      pallet_number: null,
                      contents: "",
                    },
                  ])
                }
              >
                <Plus className="h-4 w-4" /> Add box row
              </button>
            </div>
            <DeliveryVarianceSummary
              ordered={request.quantity}
              actual={actualCount}
            />
            {!allLotsConfirmed && (
              <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                Select an existing lot for every row, or use the explicit
                “Create new lot” confirmation in the lot picker. Spreadsheet
                names are preserved until you confirm them.
              </p>
            )}
            {!allPalletsConfirmed && (
              <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                Select or create each entered pallet, or clear the field to
                leave that box Unassigned. Pallets may contain boxes from
                multiple warehouses.
              </p>
            )}
            {groupedRows.error && (
              <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
                {groupedRows.error}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                className="btn-secondary"
                disabled={!readyToReview || previewMutation.isPending || pending}
                onClick={() => void reviewInventoryImpact()}
              >
                <PackageCheck className="h-4 w-4" />
                {previewMutation.isPending
                  ? "Reviewing inventory impact…"
                  : currentImpactPreview
                    ? "Review inventory impact again"
                    : "Review inventory impact"}
              </button>
              {!readyToReview && (
                <span className="text-xs text-slate-500">
                  Complete valid rows, confirm every lot, and confirm any
                  entered pallet first.
                </span>
              )}
            </div>
            {impactReview.notice && (
              <p
                role="alert"
                className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm font-medium text-amber-950"
              >
                {impactReview.notice}
              </p>
            )}
            {previewMutation.error && (
              <p role="alert" className="text-sm text-rose-600">
                {apiError(
                  previewMutation.error,
                  "Inventory impact could not be reviewed.",
                )}
              </p>
            )}
            {currentImpactPreview && (
              <InboundImpactPreview
                ordered={request.quantity}
                preview={currentImpactPreview}
                acceptRelocations={impactReview.acceptRelocations}
                onAcceptRelocations={(accepted) =>
                  setImpactReview((current) =>
                    setInboundRelocationAcceptance(current, accepted),
                  )
                }
              />
            )}
            {hasMismatch && (
              <label className="block rounded-lg border border-amber-200 bg-amber-50 p-3">
                <span className="text-sm font-medium text-amber-900">
                  Discrepancy reason
                </span>
                <span className="mt-0.5 block text-xs text-amber-800">
                  Required because inventory will record {actualCount} boxes,
                  while the order requested {request.quantity}.
                </span>
                <textarea
                  required
                  rows={3}
                  maxLength={2000}
                  className="input mt-2 bg-white"
                  placeholder={
                    variance < 0
                      ? "Explain why fewer boxes arrived"
                      : "Explain why extra boxes were accepted"
                  }
                  value={discrepancyReason}
                  onChange={(event) => setDiscrepancyReason(event.target.value)}
                />
              </label>
            )}
            <DiscrepancyEditor
              value={lineDiscrepancies}
              onChange={setLineDiscrepancies}
            />
            {error && (
              <p role="alert" className="text-sm text-rose-600">
                {error}
              </p>
            )}
            <DialogButtons
              pending={pending || previewMutation.isPending}
              pendingLabel={
                previewMutation.isPending ? "Reviewing…" : "Completing…"
              }
              disabled={
                actualCount < 1 ||
                !allLotsConfirmed ||
                !allPalletsConfirmed ||
                !!groupedRows.error ||
                !hasRequiredDiscrepancyReason(
                  request.quantity,
                  actualCount,
                  discrepancyReason,
                ) ||
                (!!currentImpactPreview && !canCompleteImpact)
              }
              confirmLabel={
                currentImpactPreview
                  ? `Complete ${currentImpactPreview.summary.created + currentImpactPreview.summary.relocated} boxes (${currentImpactPreview.summary.created} new + ${currentImpactPreview.summary.relocated} relocated)`
                  : "Review inventory impact"
              }
              onClose={onClose}
            />
          </form>
        ) : (
          <form
            className="mt-3 space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void onSubmit(
                [],
                discrepancyReason.trim() || undefined,
                [...collectedBoxIds],
                lineDiscrepancies,
              );
            }}
          >
            <div className="rounded-lg border border-brand-200 bg-brand-50 p-3">
              <p className="text-xs font-medium uppercase tracking-wider text-brand-700">
                Return destination
              </p>
              <p className="mt-0.5 text-lg font-semibold text-brand-950">
                {targetWarehouseName}
              </p>
              <p className="mt-1 text-sm text-brand-900">
                Collected boxes will move from {sourceWarehouseName} to{" "}
                {targetWarehouseName}. Unselected reservations are released
                and moved into a non-reserving follow-up draft.
              </p>
            </div>
            <div className="space-y-2">
              {request.items.map((item) => (
                <label
                  key={item.id}
                  className="flex items-center gap-3 rounded border border-slate-200 p-3"
                >
                  <input
                    type="checkbox"
                    checked={
                      item.box_id !== null && collectedBoxIds.has(item.box_id)
                    }
                    disabled={item.box_id === null}
                    onChange={() => {
                      if (item.box_id === null) return;
                      setCollectedBoxIds((current) => {
                        const next = new Set(current);
                        if (next.has(item.box_id!)) next.delete(item.box_id!);
                        else next.add(item.box_id!);
                        return next;
                      });
                    }}
                  />
                  <span className="font-mono">{item.box_number ?? `#${item.box_id}`}</span>
                  {item.lot_id ? (
                    <Link
                      to={`/lots/${item.lot_id}`}
                      className="text-sm text-brand-700 hover:underline"
                    >
                      {item.lot}
                    </Link>
                  ) : (
                    <span className="text-sm text-slate-500">{item.lot}</span>
                  )}
                </label>
              ))}
            </div>
            <p className="text-sm font-medium">
              {collectedBoxIds.size} of {request.quantity} collected
            </p>
            <label className="block">
              <span className="text-xs text-slate-500">
                Partial collection notes (optional)
              </span>
              <textarea
                className="input"
                rows={2}
                value={discrepancyReason}
                onChange={(event) => setDiscrepancyReason(event.target.value)}
              />
            </label>
            <DiscrepancyEditor
              value={lineDiscrepancies}
              onChange={setLineDiscrepancies}
              items={request.items}
            />
            {error && (
              <p role="alert" className="text-sm text-rose-600">
                {error}
              </p>
            )}
            <DialogButtons
              pending={pending}
              disabled={collectedBoxIds.size === 0}
              onClose={onClose}
              confirmLabel={
                collectedBoxIds.size === request.quantity
                  ? `Accept return at ${targetWarehouseName}`
                  : `Accept partial return at ${targetWarehouseName}`
              }
            />
          </form>
        )}
      </div>
    </div>
  );
}

function InboundImpactPreview({
  ordered,
  preview,
  acceptRelocations,
  onAcceptRelocations,
}: {
  ordered: number;
  preview: InboundCompletionPreview;
  acceptRelocations: boolean;
  onAcceptRelocations: (accepted: boolean) => void;
}) {
  const created = preview.rows.filter(
    (row) => row.classification === "create",
  );
  const relocated = preview.rows.filter(
    (row) => row.classification === "relocate",
  );
  const blocked = preview.rows.filter(
    (row) => row.classification === "blocked",
  );
  const counts = inboundCompletionCounts(
    ordered,
    preview,
    acceptRelocations,
  );
  const varianceLabel =
    counts.variance === 0
      ? "matches the ordered quantity"
      : counts.variance < 0
        ? `short by ${Math.abs(counts.variance)}`
        : `over by ${counts.variance}`;

  return (
    <section className="space-y-4 rounded-lg border border-slate-300 bg-white p-4">
      <div>
        <h3 className="font-semibold text-slate-900">Inventory impact</h3>
        <p className="text-xs text-slate-500">
          Reviewed against request version {preview.request_version}.
        </p>
      </div>
      <dl className="grid grid-cols-3 gap-3">
        <ImpactMetric
          label="New boxes"
          value={preview.summary.created}
          tone="text-emerald-700"
        />
        <ImpactMetric
          label="Existing boxes to relocate"
          value={preview.summary.relocated}
          tone="text-sky-700"
        />
        <ImpactMetric
          label="Blocked"
          value={preview.summary.blocked}
          tone={preview.summary.blocked > 0 ? "text-rose-700" : "text-slate-700"}
        />
      </dl>

      {created.length > 0 && (
        <ImpactRows title="New boxes">
          {created.map((row) => (
            <li key={impactRowKey(row)} className="rounded bg-emerald-50 p-2">
              <strong className="font-mono">{row.box_number}</strong>
              {" · "}
              lot {row.lot}
              {" → "}
              box warehouse {row.target_warehouse_name} · pallet{" "}
              {inboundTargetPalletLabel(row)}
              {row.target_pallet_resolution.resolution === "will_create"
                ? " (new pallet)"
                : ""}
            </li>
          ))}
        </ImpactRows>
      )}

      {relocated.length > 0 && (
        <>
          <ImpactRows title="Existing boxes to relocate">
            {relocated.map((row) => (
              <li key={impactRowKey(row)} className="rounded bg-sky-50 p-2">
                <strong className="font-mono">{row.box_number}</strong>
                {" · "}
                lot {row.lot}
                {" · current pallet "}
                {row.current_pallet_number ?? "Unassigned"}
                <span className="mt-0.5 block">
                  {row.source_warehouse_name ?? "Unknown warehouse"}
                  {" → "}
                  {row.target_warehouse_name}
                  {" · target pallet "}
                  {inboundTargetPalletLabel(row)}
                </span>
              </li>
            ))}
          </ImpactRows>
          <label className="flex items-start gap-3 rounded-lg border border-sky-300 bg-sky-50 p-3">
            <input
              type="checkbox"
              className="mt-1"
              checked={acceptRelocations}
              onChange={(event) => onAcceptRelocations(event.target.checked)}
            />
            <span>
              <strong className="text-sm text-sky-950">
                Move all {preview.summary.relocated} eligible matched boxes
              </strong>
              <span className="mt-1 block text-xs text-sky-900">
                This applies to every matched box; there is no per-row
                selection and no source-warehouse access is required. Only
                received, unreserved boxes in another warehouse qualify. Their
                status and original receipt history and timestamp stay
                unchanged; only each box’s warehouse and pallet assignment
                change. The pallet itself has no warehouse location, so keeping
                the same pallet across source and target warehouses is valid.
              </span>
            </span>
          </label>
        </>
      )}

      {blocked.length > 0 && (
        <ImpactRows title="Blocked rows">
          {blocked.map((row) => (
            <li
              key={impactRowKey(row)}
              className="rounded border border-rose-200 bg-rose-50 p-2 text-rose-900"
            >
              <strong className="font-mono">{row.box_number}</strong>
              {" · "}
              lot {row.lot}
              <span className="mt-0.5 block text-xs font-medium uppercase tracking-wide">
                {row.blocked_code ?? "blocked"}
              </span>
              <span className="block text-sm">{inboundBlockedMessage(row)}</span>
            </li>
          ))}
        </ImpactRows>
      )}

      <div
        className={`rounded-lg border p-3 text-sm ${
          preview.can_complete
            ? "border-brand-200 bg-brand-50 text-brand-950"
            : "border-rose-200 bg-rose-50 text-rose-950"
        }`}
      >
        <strong className="block">Final confirmation</strong>
        {preview.can_complete ? (
          <>
            <span className="block">
              {counts.eligible} boxes will be completed: {counts.created} new +{" "}
              {counts.relocated} relocated
              {counts.relocated > 0
                ? acceptRelocations
                  ? " (accepted)"
                  : " (relocation acceptance required)"
                : ""}
              .
            </span>
            <span className="block">
              Current accepted count: {counts.accepted} of {counts.eligible}.
            </span>
            <span className="block">
              Ordered {ordered}; this delivery {varianceLabel}.
            </span>
          </>
        ) : (
          <span>
            Completion is blocked until all {counts.blocked} blocked rows are
            resolved and the impact is reviewed again.
          </span>
        )}
      </div>
    </section>
  );
}

function ImpactMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: string;
}) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={`text-xl font-semibold tabular-nums ${tone}`}>{value}</dd>
    </div>
  );
}

function ImpactRows({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div>
      <h4 className="text-sm font-medium text-slate-800">{title}</h4>
      <ul className="mt-2 space-y-2 text-sm">{children}</ul>
    </div>
  );
}

function impactRowKey(row: InboundCompletionPreviewRow): string {
  return `${row.normalized_lot}\u0000${row.normalized_box_number}`;
}

function DeliveryVarianceSummary({
  ordered,
  actual,
}: {
  ordered: number;
  actual: number;
}) {
  const variance = deliveryVariance(ordered, actual);
  const tone =
    variance === 0
      ? "border-emerald-200 bg-emerald-50 text-emerald-900"
      : variance < 0
        ? "border-amber-200 bg-amber-50 text-amber-900"
        : "border-sky-200 bg-sky-50 text-sky-900";
  const result =
    variance === 0
      ? "Matches order"
      : variance < 0
        ? `Short by ${Math.abs(variance)}`
        : `Over by ${variance}`;
  return (
    <div className={`rounded-lg border p-3 ${tone}`}>
      <div className="grid grid-cols-3 gap-3 text-sm">
        <div>
          <span className="block text-xs opacity-70">Ordered</span>
          <strong className="tabular-nums">{ordered}</strong>
        </div>
        <div>
          <span className="block text-xs opacity-70">Actual unique boxes</span>
          <strong className="tabular-nums">{actual}</strong>
        </div>
        <div>
          <span className="block text-xs opacity-70">Result</span>
          <strong>{result}</strong>
        </div>
      </div>
    </div>
  );
}

function DialogButtons({
  pending,
  disabled = false,
  confirmLabel = "Complete request",
  pendingLabel = "Completing…",
  onClose,
  onConfirm,
}: {
  pending: boolean;
  disabled?: boolean;
  confirmLabel?: string;
  pendingLabel?: string;
  onClose: () => void;
  onConfirm?: () => void;
}) {
  return (
    <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
      <button
        type="button"
        className="btn-secondary"
        disabled={pending}
        onClick={onClose}
      >
        Cancel
      </button>
      <button
        type={onConfirm ? "button" : "submit"}
        className="btn-primary"
        disabled={pending || disabled}
        onClick={onConfirm}
      >
        <PackageCheck className="h-4 w-4" />
        {pending ? pendingLabel : confirmLabel}
      </button>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wider text-slate-400">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

function eventLabel(
  event: import("@/api/types").RequestEvent,
  direction?: BoxRequest["direction"],
): string {
  if (direction) {
    const label = {
      preparation_started:
        direction === "inbound" ? "Preparing inbound" : "Preparing return",
      ready_for_transport:
        direction === "inbound"
          ? "Ready for dispatch"
          : "Ready for collection",
      in_transit: direction === "inbound" ? "Delivering" : "Collecting",
      awaiting_confirmation:
        direction === "inbound"
          ? "Delivered · awaiting requester confirmation"
          : "Collected · awaiting warehouse confirmation",
      failed_delivery:
        direction === "inbound" ? "Delivery attempt failed" : "Collection attempt failed",
      transport_retry:
        direction === "inbound" ? "Delivery retry prepared" : "Collection retry prepared",
    }[event.event_type];
    if (label) return label;
  }
  if (event.from_status === event.to_status) {
    return humanize(event.event_type);
  }
  if (event.from_status && event.to_status) {
    return `${humanize(event.from_status)} → ${humanize(event.to_status)}`;
  }
  return humanize(event.event_type);
}

function documentTypeLabel(type: RequestDocumentType): string {
  return type === "delivery_note"
    ? "Delivery note"
    : type === "return_note"
      ? "Return note"
      : "Other";
}

function humanize(value: string): string {
  const text = value.replace(/[._-]+/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

function localDateInput(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function apiError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (
    detail &&
    typeof detail === "object" &&
    "message" in detail &&
    typeof detail.message === "string"
  ) {
    return detail.message;
  }
  return fallback;
}
