import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Check,
  Download,
  FileText,
  FileSpreadsheet,
  PackageCheck,
  Play,
  Plus,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  useDownloadRequestDocument,
  useMe,
  useRequest,
  useRequestAction,
  useRequestDocuments,
  useRequestEvents,
  usePreviewBoxImportXlsx,
  usePreviewInboundXlsx,
  useUploadRequestDocument,
  useWarehouses,
} from "@/api/hooks";
import type {
  BoxRequest,
  InboundRequestItemInput,
  RequestDocument,
  RequestDocumentType,
  XlsxPreviewSheet,
} from "@/api/types";
import {
  REQUEST_DIRECTION_LABEL,
  RequestStatusBadge,
} from "@/components/RequestStatusBadge";
import { requestPermissions } from "@/pages/requestPermissions";
import {
  deliveryVariance,
  groupInboundItems,
  hasRequiredDiscrepancyReason,
} from "@/pages/xlsxMapping";

type ReasonDialog = "reject" | "cancel" | null;

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
  const [showCompletion, setShowCompletion] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

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
  const warehouseName =
    warehouses.data?.find((warehouse) => warehouse.id === request.warehouse_id)
      ?.name ?? `#${request.warehouse_id}`;
  const {
    canMove,
    canApprove,
    canUpload,
    canCancel,
    canComplete,
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
  ): Promise<boolean> {
    setActionError(null);
    try {
      await action.mutateAsync(input);
      return true;
    } catch (caught) {
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
            <p className="mt-0.5 text-sm text-slate-500">
              {REQUEST_DIRECTION_LABEL[request.direction]} · {warehouseName}
            </p>
            {request.direction === "return" &&
              request.source_inbound_request_id !== null && (
                <Link
                  to={`/requests/${request.source_inbound_request_id}`}
                  className="mt-1 inline-block text-sm text-brand-700 hover:underline"
                >
                  Reverses inbound order #{request.source_inbound_request_id}
                </Link>
              )}
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
          <Field label="Submitted">
            {formatDate(request.submitted_at ?? request.created_at)}
          </Field>
          <Field label="Last updated">{formatDate(request.updated_at)}</Field>
        </dl>

        <InventorySnapshot request={request} />

        {(request.rejection_reason || request.cancellation_reason) && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
            <strong>
              {request.rejection_reason ? "Rejection reason:" : "Cancellation reason:"}
            </strong>{" "}
            {request.rejection_reason ?? request.cancellation_reason}
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

        {(canMove || canCancel || canComplete) && (
          <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-3">
            {canApprove && (
              <>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={action.isPending}
                  onClick={() =>
                    void perform({
                      id: request.id,
                      expectedVersion: request.version,
                      action: "approve",
                    })
                  }
                >
                  <Check className="h-4 w-4" /> Approve
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
            {canMove && request.status === "approved" && (
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
                  ? "Accept delivery"
                  : "Accept return"}
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
      </header>

      {request.items.length > 0 && (
        <ItemsSection request={request} warehouseName={warehouseName} />
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Timeline
          events={events.data ?? []}
          loading={events.isLoading}
        />
        <DocumentsSection
          requestId={request.id}
          direction={request.direction}
          documents={visibleDocuments}
          loading={documents.isLoading}
          canUpload={canUpload}
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
      {showCompletion && (
        <CompletionDialog
          request={request}
          pending={action.isPending}
          error={actionError}
          onClose={() => setShowCompletion(false)}
          onSubmit={async (items, discrepancyReason) => {
            const ok = await perform({
              id: request.id,
              expectedVersion: request.version,
              action: "complete",
              body:
                request.direction === "inbound"
                  ? {
                      inbound_items: items,
                      discrepancy_reason: discrepancyReason || undefined,
                    }
                  : {},
            });
            if (ok) setShowCompletion(false);
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
}: {
  request: BoxRequest;
  warehouseName: string;
}) {
  return (
    <section className="card overflow-hidden">
      <header className="border-b border-slate-100 px-5 py-3">
        <h2 className="font-semibold">Boxes</h2>
        <p className="text-xs text-slate-500">
          {request.direction === "inbound"
            ? `Boxes received at ${warehouseName}.`
            : `Boxes selected for this return from ${warehouseName}.`}
        </p>
      </header>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500">
            <tr>
              <th className="px-4 py-2.5">Box #</th>
              <th className="px-4 py-2.5">Lot</th>
              <th className="px-4 py-2.5">Contents</th>
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
                <td className="px-4 py-2.5">{item.lot ?? "—"}</td>
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

function Timeline({
  events,
  loading,
}: {
  events: import("@/api/types").RequestEvent[];
  loading: boolean;
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
                  {eventLabel(event)}
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
}: {
  requestId: number;
  direction: BoxRequest["direction"];
  documents: RequestDocument[];
  loading: boolean;
  canUpload: boolean;
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
            <p role="alert" className="text-sm text-rose-600">
              {error}
            </p>
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

function CompletionDialog({
  request,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  request: BoxRequest;
  pending: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (
    items: InboundRequestItemInput[],
    discrepancyReason?: string,
  ) => Promise<void>;
}) {
  const inbound = request.direction === "inbound";
  const [rows, setRows] = useState<InboundRequestItemInput[]>(() =>
    inbound
      ? Array.from({ length: request.quantity }, () => ({
          lot: "",
          box_number: "",
          contents: "",
        }))
      : [],
  );
  const [showExcelMapper, setShowExcelMapper] = useState(false);
  const [discrepancyReason, setDiscrepancyReason] = useState("");
  const actualCount = groupInboundItems(
    rows.filter((row) => row.box_number.trim() && row.lot.trim()),
  ).length;
  const variance = deliveryVariance(request.quantity, actualCount);
  const hasMismatch = variance !== 0;

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
            onSubmit={(event) => {
              event.preventDefault();
              void onSubmit(
                rows.map((row) => ({
                  lot: row.lot.trim(),
                  box_number: row.box_number.trim(),
                  contents: row.contents?.trim() || undefined,
                })),
                hasMismatch ? discrepancyReason.trim() || undefined : undefined,
              );
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
                  requestId={request.id}
                  quantity={request.quantity}
                  onApply={(mappedRows) => {
                    setRows(mappedRows);
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
                        setRows((current) =>
                          current.filter((_, rowIndex) => rowIndex !== index),
                        )
                      }
                    >
                      <Trash2 className="h-4 w-4" /> Remove
                    </button>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-3">
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
                    <label className="block">
                      <span className="text-xs text-slate-500">Lot</span>
                      <input
                        required
                        className="input"
                        value={row.lot}
                        onChange={(event) =>
                          updateRow(index, "lot", event.target.value)
                        }
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">
                        Contents (optional)
                      </span>
                      <input
                        className="input"
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
                    { lot: "", box_number: "", contents: "" },
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
            {error && (
              <p role="alert" className="text-sm text-rose-600">
                {error}
              </p>
            )}
            <DialogButtons
              pending={pending}
              disabled={
                actualCount < 1 ||
                !hasRequiredDiscrepancyReason(
                  request.quantity,
                  actualCount,
                  discrepancyReason,
                )
              }
              confirmLabel={
                hasMismatch ? "Accept with discrepancy" : "Accept delivery"
              }
              onClose={onClose}
            />
          </form>
        ) : (
          <div className="mt-3 space-y-4">
            <p className="text-sm text-slate-600">
              Confirm that all {request.quantity} boxes in this return request
              reached their destination. This marks the request and its boxes as
              completed.
            </p>
            {error && (
              <p role="alert" className="text-sm text-rose-600">
                {error}
              </p>
            )}
            <DialogButtons
              pending={pending}
              onClose={onClose}
              onConfirm={() => onSubmit([])}
            />
          </div>
        )}
      </div>
    </div>
  );
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

export function ExcelRowMapper({
  requestId,
  quantity,
  onApply,
}: {
  requestId?: number;
  quantity?: number;
  onApply: (rows: InboundRequestItemInput[]) => void;
}) {
  const requestPreview = usePreviewInboundXlsx();
  const boxImportPreview = usePreviewBoxImportXlsx();
  const preview = requestId === undefined ? boxImportPreview : requestPreview;
  const [file, setFile] = useState<File | null>(null);
  const [sheetName, setSheetName] = useState("");
  const [boxColumn, setBoxColumn] = useState<number | undefined>();
  const [lotSource, setLotSource] = useState<"fixed" | "column">("fixed");
  const [lotColumn, setLotColumn] = useState<number | undefined>();
  const [fixedLot, setFixedLot] = useState("");
  const [contentsColumn, setContentsColumn] = useState<number | undefined>();
  const [selectedRows, setSelectedRows] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const sheet = preview.data?.sheets.find(
    (candidate) => candidate.name === sheetName,
  );
  const selectedMappedRows: InboundRequestItemInput[] = sheet
    ? sheet.rows
        .filter((row) => selectedRows.has(row.row_number))
        .map((row) => ({
          box_number:
            boxColumn === undefined ? "" : (row.cells[boxColumn] ?? "").trim(),
          lot:
            lotSource === "fixed"
              ? fixedLot.trim()
              : lotColumn === undefined
                ? ""
                : (row.cells[lotColumn] ?? "").trim(),
          contents:
            contentsColumn === undefined
              ? undefined
              : (row.cells[contentsColumn] ?? "").trim() || undefined,
        }))
    : [];
  const groupedSelectionCount = groupInboundItems(
    selectedMappedRows.filter((row) => row.box_number && row.lot),
  ).length;

  function resetMapping(nextSheetName: string, rowNumbers: number[]) {
    setSheetName(nextSheetName);
    setBoxColumn(undefined);
    setLotColumn(undefined);
    setContentsColumn(undefined);
    setSelectedRows(new Set(rowNumbers));
    setError(null);
  }

  async function loadWorkbook() {
    if (!file) return;
    setError(null);
    try {
      const result =
        requestId === undefined
          ? await boxImportPreview.mutateAsync({ file })
          : await requestPreview.mutateAsync({ requestId, file });
      const firstSheet = result.sheets[0];
      resetMapping(
        firstSheet?.name ?? "",
        firstSheet?.rows.map((row) => row.row_number) ?? [],
      );
    } catch (caught) {
      setError(apiError(caught, "Could not read the workbook."));
    }
  }

  function toggleRow(rowNumber: number) {
    setSelectedRows((current) => {
      const next = new Set(current);
      if (next.has(rowNumber)) next.delete(rowNumber);
      else next.add(rowNumber);
      return next;
    });
  }

  function applyMapping() {
    if (!sheet || boxColumn === undefined) {
      setError("Choose the column containing the box number.");
      return;
    }
    if (lotSource === "fixed" && !fixedLot.trim()) {
      setError("Enter the lot value to apply to the selected rows.");
      return;
    }
    if (lotSource === "column" && lotColumn === undefined) {
      setError("Choose the column containing the lot.");
      return;
    }
    if (
      lotSource === "column" &&
      lotColumn === boxColumn
    ) {
      setError("Box number and lot must use different columns.");
      return;
    }
    if (selectedRows.size === 0) {
      setError("Select at least one workbook row.");
      return;
    }

    const mapped: InboundRequestItemInput[] = [];
    for (const row of sheet.rows) {
      if (!selectedRows.has(row.row_number)) continue;
      const boxNumber = (row.cells[boxColumn] ?? "").trim();
      const lot =
        lotSource === "fixed"
          ? fixedLot.trim()
          : (row.cells[lotColumn!] ?? "").trim();
      if (!boxNumber || !lot) {
        setError(
          `Excel row ${row.row_number} is missing a mapped box number or lot.`,
        );
        return;
      }
      if (!/^\d+$/.test(boxNumber)) {
        setError(
          `Excel row ${row.row_number} has a non-numeric mapped box number.`,
        );
        return;
      }
      const contents =
        contentsColumn === undefined
          ? ""
          : (row.cells[contentsColumn] ?? "").trim();
      mapped.push({
        box_number: boxNumber,
        lot,
        contents: contents || undefined,
      });
    }
    const grouped = groupInboundItems(mapped);
    const oversized = grouped.find((item) => (item.contents?.length ?? 0) > 200);
    if (oversized) {
      setError(
        `Combined contents for box ${oversized.box_number} exceed 200 characters.`,
      );
      return;
    }
    setError(null);
    onApply(grouped);
  }

  return (
    <div className="mt-4 space-y-4 border-t border-slate-200 pt-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="block flex-1">
          <span className="text-xs text-slate-500">Excel workbook</span>
          <input
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            className="input file:mr-3 file:border-0 file:bg-transparent file:text-sm"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setError(null);
            }}
          />
        </label>
        <button
          type="button"
          className="btn-secondary"
          disabled={!file || preview.isPending}
          onClick={() => void loadWorkbook()}
        >
          <Upload className="h-4 w-4" />
          {preview.isPending ? "Reading…" : "Preview workbook"}
        </button>
      </div>

      {preview.data && sheet && (
        <>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <label className="block">
              <span className="text-xs text-slate-500">Worksheet</span>
              <select
                className="input"
                value={sheetName}
                onChange={(event) => {
                  const nextSheet = preview.data?.sheets.find(
                    (candidate) => candidate.name === event.target.value,
                  );
                  resetMapping(
                    event.target.value,
                    nextSheet?.rows.map((row) => row.row_number) ?? [],
                  );
                }}
              >
                {preview.data.sheets.map((candidate) => (
                  <option key={candidate.name} value={candidate.name}>
                    {candidate.name}
                  </option>
                ))}
              </select>
            </label>
            <ColumnSelect
              label="Box number column"
              sheet={sheet}
              value={boxColumn}
              required
              onChange={setBoxColumn}
            />
            <label className="block">
              <span className="text-xs text-slate-500">Lot source</span>
              <select
                className="input"
                value={lotSource}
                onChange={(event) =>
                  setLotSource(event.target.value as "fixed" | "column")
                }
              >
                <option value="fixed">One fixed lot value</option>
                <option value="column">Workbook column</option>
              </select>
            </label>
            {lotSource === "fixed" ? (
              <label className="block">
                <span className="text-xs text-slate-500">Fixed lot</span>
                <input
                  className="input"
                  placeholder="For example PR100"
                  maxLength={64}
                  value={fixedLot}
                  onChange={(event) => setFixedLot(event.target.value)}
                />
              </label>
            ) : (
              <ColumnSelect
                label="Lot column"
                sheet={sheet}
                value={lotColumn}
                required
                onChange={setLotColumn}
              />
            )}
            <ColumnSelect
              label="Contents column (optional)"
              sheet={sheet}
              value={contentsColumn}
              onChange={setContentsColumn}
            />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600">
            <span>
              All rows are included initially. Uncheck headers and extra data
              to exclude them. Repeated box numbers are merged.
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="text-brand-700 underline hover:text-brand-800"
                onClick={() =>
                  setSelectedRows(
                    new Set(sheet.rows.map((row) => row.row_number)),
                  )
                }
              >
                Include all
              </button>
              <button
                type="button"
                className="text-slate-600 underline hover:text-slate-800"
                onClick={() => setSelectedRows(new Set())}
              >
                Exclude all
              </button>
              <span className="font-medium tabular-nums">
                {selectedRows.size} rows → {groupedSelectionCount} unique boxes
              </span>
            </div>
          </div>
          {quantity !== undefined && (
            <DeliveryVarianceSummary
              ordered={quantity}
              actual={groupedSelectionCount}
            />
          )}

          <div className="max-h-80 overflow-auto rounded-lg border border-slate-200 bg-white">
            <table className="min-w-full border-collapse text-xs">
              <thead className="sticky top-0 z-10 bg-slate-100 text-left text-slate-600">
                <tr>
                  <th className="w-16 border-b px-2 py-2">Included</th>
                  <th className="w-14 border-b px-2 py-2">Row</th>
                  {Array.from({ length: sheet.max_columns }, (_, index) => (
                    <th
                      key={index}
                      className="min-w-32 border-b border-l px-2 py-2"
                    >
                      Column {excelColumnName(index)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sheet.rows.map((row) => {
                  const checked = selectedRows.has(row.row_number);
                  return (
                    <tr
                      key={row.row_number}
                      className={checked ? "bg-brand-50" : "hover:bg-slate-50"}
                    >
                      <td className="border-b px-2 py-1.5 text-center">
                        <input
                          type="checkbox"
                          checked={checked}
                          aria-label={`Use Excel row ${row.row_number}`}
                          onChange={() => toggleRow(row.row_number)}
                        />
                      </td>
                      <td className="border-b px-2 py-1.5 font-mono text-slate-500">
                        {row.row_number}
                      </td>
                      {Array.from(
                        { length: sheet.max_columns },
                        (_, columnIndex) => (
                          <td
                            key={columnIndex}
                            className={`max-w-64 truncate border-b border-l px-2 py-1.5 ${
                              columnIndex === boxColumn ||
                              columnIndex === lotColumn ||
                              columnIndex === contentsColumn
                                ? "bg-amber-50"
                                : ""
                            }`}
                            title={row.cells[columnIndex] ?? ""}
                          >
                            {row.cells[columnIndex] || "—"}
                          </td>
                        ),
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="flex justify-end">
            <button
              type="button"
              className="btn-primary"
              disabled={
                selectedRows.size === 0 || groupedSelectionCount === 0
              }
              onClick={applyMapping}
            >
              Apply mapping and review
            </button>
          </div>
        </>
      )}

      {error && (
        <p role="alert" className="text-sm text-rose-600">
          {error}
        </p>
      )}
    </div>
  );
}

function ColumnSelect({
  label,
  sheet,
  value,
  required = false,
  onChange,
}: {
  label: string;
  sheet: XlsxPreviewSheet;
  value: number | undefined;
  required?: boolean;
  onChange: (value: number | undefined) => void;
}) {
  return (
    <label className="block">
      <span className="text-xs text-slate-500">{label}</span>
      <select
        className="input"
        value={value ?? ""}
        onChange={(event) =>
          onChange(
            event.target.value === "" ? undefined : Number(event.target.value),
          )
        }
      >
        <option value="">{required ? "Choose a column" : "Do not import"}</option>
        {Array.from({ length: sheet.max_columns }, (_, index) => {
          const examples = sheet.rows
            .map((row) => row.cells[index])
            .filter(Boolean)
            .slice(0, 3)
            .join(" · ");
          return (
            <option key={index} value={index}>
              {excelColumnName(index)}
              {examples ? ` — ${examples.slice(0, 70)}` : ""}
            </option>
          );
        })}
      </select>
    </label>
  );
}

function excelColumnName(index: number): string {
  let value = index + 1;
  let name = "";
  while (value > 0) {
    value -= 1;
    name = String.fromCharCode(65 + (value % 26)) + name;
    value = Math.floor(value / 26);
  }
  return name;
}

function DialogButtons({
  pending,
  disabled = false,
  confirmLabel = "Complete request",
  onClose,
  onConfirm,
}: {
  pending: boolean;
  disabled?: boolean;
  confirmLabel?: string;
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
        {pending ? "Completing…" : confirmLabel}
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
): string {
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

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function apiError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}
