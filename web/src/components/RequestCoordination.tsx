import { useEffect, useMemo, useState } from "react";
import { Download, MessageSquare, Paperclip, Save } from "lucide-react";
import {
  useAddRequestComment,
  useDownloadRequestAttachment,
  useRequestAssignees,
  useRequestAttachments,
  useRequestComments,
  useUpdateRequestCoordination,
  useUploadRequestAttachment,
} from "@/api/hooks";
import type { BoxRequest, RequestPriority } from "@/api/types";
import {
  toLocalDateTimeInput,
  toUtcDateTime,
  validTransportWindow,
} from "@/pages/requestCoordination";

const PRIORITIES: RequestPriority[] = ["low", "normal", "high", "urgent"];

export function RequestCoordinationPanel({
  request,
}: {
  request: BoxRequest;
}) {
  const update = useUpdateRequestCoordination();
  const assignees = useRequestAssignees(
    request.permissions.can_assign ? request.warehouse_id : undefined,
  );
  const [priority, setPriority] = useState(request.priority);
  const [requestedDate, setRequestedDate] = useState(request.requested_date ?? "");
  const [windowStart, setWindowStart] = useState(
    toLocalDateTimeInput(request.scheduled_window_start),
  );
  const [windowEnd, setWindowEnd] = useState(
    toLocalDateTimeInput(request.scheduled_window_end),
  );
  const [slaDeadline, setSlaDeadline] = useState(
    toLocalDateTimeInput(request.sla_deadline),
  );
  const [assigneeId, setAssigneeId] = useState(
    request.assigned_mover_user_id?.toString() ?? "",
  );
  const [contact, setContact] = useState(request.destination_contact ?? "");
  const [location, setLocation] = useState(request.internal_location ?? "");
  const [instructions, setInstructions] = useState(
    request.special_handling_instructions ?? "",
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPriority(request.priority);
    setRequestedDate(request.requested_date ?? "");
    setWindowStart(toLocalDateTimeInput(request.scheduled_window_start));
    setWindowEnd(toLocalDateTimeInput(request.scheduled_window_end));
    setSlaDeadline(toLocalDateTimeInput(request.sla_deadline));
    setAssigneeId(request.assigned_mover_user_id?.toString() ?? "");
    setContact(request.destination_contact ?? "");
    setLocation(request.internal_location ?? "");
    setInstructions(request.special_handling_instructions ?? "");
  }, [request]);

  const editable =
    request.permissions.can_assign || request.permissions.can_schedule;
  const dirty = useMemo(
    () =>
      priority !== request.priority ||
      requestedDate !== (request.requested_date ?? "") ||
      windowStart !== toLocalDateTimeInput(request.scheduled_window_start) ||
      windowEnd !== toLocalDateTimeInput(request.scheduled_window_end) ||
      slaDeadline !== toLocalDateTimeInput(request.sla_deadline) ||
      assigneeId !==
        (request.assigned_mover_user_id?.toString() ?? "") ||
      contact !== (request.destination_contact ?? "") ||
      location !== (request.internal_location ?? "") ||
      instructions !== (request.special_handling_instructions ?? ""),
    [
      assigneeId,
      contact,
      instructions,
      location,
      priority,
      request,
      requestedDate,
      slaDeadline,
      windowEnd,
      windowStart,
    ],
  );

  return (
    <section className="card overflow-hidden">
      <header className="border-b border-slate-100 px-5 py-3">
        <h2 className="font-semibold">Coordination</h2>
        <p className="text-xs text-slate-500">
          Assignment and transport planning. Every change is versioned and
          recorded in the timeline.
        </p>
      </header>
      <div className="grid gap-3 p-5 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Priority">
          <select
            className="input"
            value={priority}
            disabled={!editable}
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
        </Field>
        <Field label="Assigned mover">
          {editable ? (
            <select
              className="input"
              value={assigneeId}
              onChange={(event) => setAssigneeId(event.target.value)}
            >
              <option value="">Unassigned</option>
              {assignees.data?.map((assignee) => (
                <option key={assignee.id} value={assignee.id}>
                  {assignee.display_name} · {assignee.role.replace("_", " ")}
                </option>
              ))}
            </select>
          ) : (
            <Value>{request.assigned_mover_name ?? "Unassigned"}</Value>
          )}
        </Field>
        <Field label="Requested date">
          <input
            className="input"
            type="date"
            value={requestedDate}
            disabled={!editable}
            onChange={(event) => setRequestedDate(event.target.value)}
          />
        </Field>
        <Field label="SLA deadline">
          <input
            className="input"
            type="datetime-local"
            value={slaDeadline}
            disabled={!editable}
            onChange={(event) => setSlaDeadline(event.target.value)}
          />
        </Field>
        <Field label="Transport window start">
          <input
            className="input"
            type="datetime-local"
            value={windowStart}
            disabled={!request.permissions.can_schedule}
            onChange={(event) => setWindowStart(event.target.value)}
          />
        </Field>
        <Field label="Transport window end">
          <input
            className="input"
            type="datetime-local"
            value={windowEnd}
            disabled={!request.permissions.can_schedule}
            onChange={(event) => setWindowEnd(event.target.value)}
          />
        </Field>
        <Field label="Destination contact">
          <input
            className="input"
            value={contact}
            maxLength={320}
            disabled={!editable}
            onChange={(event) => setContact(event.target.value)}
          />
        </Field>
        <Field label="Internal location">
          <input
            className="input"
            value={location}
            maxLength={320}
            disabled={!editable}
            onChange={(event) => setLocation(event.target.value)}
          />
        </Field>
        <label className="block sm:col-span-2 lg:col-span-4">
          <span className="text-xs text-slate-500">
            Special handling instructions
          </span>
          <textarea
            className="input min-h-20"
            value={instructions}
            maxLength={5000}
            disabled={!editable}
            onChange={(event) => setInstructions(event.target.value)}
          />
        </label>
        {error && (
          <p className="text-sm text-rose-600 sm:col-span-2 lg:col-span-4">
            {error}
          </p>
        )}
        {editable && (
          <div className="flex justify-end sm:col-span-2 lg:col-span-4">
            <button
              type="button"
              className="btn-primary"
              disabled={!dirty || update.isPending}
              onClick={async () => {
                setError(null);
                if (!validTransportWindow(windowStart, windowEnd)) {
                  setError(
                    windowStart
                      ? "Transport window end must be after its start."
                      : "Set a transport window start before the end.",
                  );
                  return;
                }
                try {
                  await update.mutateAsync({
                    id: request.id,
                    expectedVersion: request.version,
                    patch: {
                      priority,
                      requested_date: requestedDate || null,
                      scheduled_window_start: toUtcDateTime(windowStart),
                      scheduled_window_end: toUtcDateTime(windowEnd),
                      sla_deadline: toUtcDateTime(slaDeadline),
                      assigned_mover_user_id: assigneeId
                        ? Number(assigneeId)
                        : null,
                      destination_contact: contact || null,
                      internal_location: location || null,
                      special_handling_instructions: instructions || null,
                    },
                  });
                } catch (caught) {
                  setError(apiError(caught, "Coordination changes could not be saved."));
                }
              }}
            >
              <Save className="h-4 w-4" />
              {update.isPending ? "Saving…" : "Save coordination"}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

export function RequestDiscussionPanel({
  request,
}: {
  request: BoxRequest;
}) {
  const comments = useRequestComments(request.id);
  const attachments = useRequestAttachments(request.id);
  const addComment = useAddRequestComment();
  const upload = useUploadRequestAttachment();
  const download = useDownloadRequestAttachment();
  const [body, setBody] = useState("");
  const [file, setFile] = useState<File>();
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <section className="card overflow-hidden">
        <header className="flex items-center gap-2 border-b border-slate-100 px-5 py-3">
          <MessageSquare className="h-4 w-4 text-slate-500" />
          <h2 className="font-semibold">Discussion</h2>
        </header>
        <div className="max-h-80 divide-y divide-slate-100 overflow-y-auto">
          {(comments.data ?? request.comments).map((comment) => (
            <article key={comment.id} className="px-5 py-3">
              <div className="flex justify-between gap-3 text-xs text-slate-500">
                <strong className="text-slate-700">{comment.author_name}</strong>
                <time>{new Date(comment.created_at).toLocaleString()}</time>
              </div>
              <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700">
                {comment.body}
              </p>
            </article>
          ))}
          {!comments.isLoading &&
            (comments.data ?? request.comments).length === 0 && (
              <p className="px-5 py-4 text-sm text-slate-500">
                No comments yet.
              </p>
            )}
        </div>
        {request.permissions.can_comment && (
          <form
            className="border-t border-slate-100 p-4"
            onSubmit={async (event) => {
              event.preventDefault();
              if (!body.trim()) return;
              setError(null);
              try {
                await addComment.mutateAsync({
                  requestId: request.id,
                  expectedVersion: request.version,
                  body: body.trim(),
                });
                setBody("");
              } catch (caught) {
                setError(apiError(caught, "Comment could not be added."));
              }
            }}
          >
            <textarea
              className="input min-h-20"
              value={body}
              maxLength={5000}
              placeholder="Add a coordination note…"
              onChange={(event) => setBody(event.target.value)}
            />
            <button
              type="submit"
              className="btn-primary mt-2"
              disabled={!body.trim() || addComment.isPending}
            >
              Add comment
            </button>
          </form>
        )}
      </section>

      <section className="card overflow-hidden">
        <header className="flex items-center gap-2 border-b border-slate-100 px-5 py-3">
          <Paperclip className="h-4 w-4 text-slate-500" />
          <div>
            <h2 className="font-semibold">Supporting attachments</h2>
            <p className="text-xs text-slate-500">
              Non-ERP PDFs, images, text, CSV, XLSX, or DOCX files.
            </p>
          </div>
        </header>
        <div className="divide-y divide-slate-100">
          {(attachments.data ?? request.attachments).map((attachment) => (
            <div
              key={attachment.id}
              className="flex items-center justify-between gap-3 px-5 py-3"
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">
                  {attachment.original_filename}
                </div>
                <div className="text-xs text-slate-500">
                  {(attachment.size_bytes / 1024).toFixed(1)} KB
                </div>
              </div>
              <button
                type="button"
                className="btn-ghost"
                onClick={() =>
                  download.mutate({
                    requestId: request.id,
                    attachmentId: attachment.id,
                    filename: attachment.original_filename,
                  })
                }
              >
                <Download className="h-4 w-4" />
                <span className="sr-only">Download</span>
              </button>
            </div>
          ))}
          {!attachments.isLoading &&
            (attachments.data ?? request.attachments).length === 0 && (
              <p className="px-5 py-4 text-sm text-slate-500">
                No supporting attachments.
              </p>
            )}
        </div>
        {request.permissions.can_attach && (
          <div className="border-t border-slate-100 p-4">
            <input
              className="input"
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.txt,.csv,.xlsx,.docx"
              onChange={(event) => setFile(event.target.files?.[0])}
            />
            <button
              type="button"
              className="btn-primary mt-2"
              disabled={!file || upload.isPending}
              onClick={async () => {
                if (!file) return;
                setError(null);
                try {
                  await upload.mutateAsync({
                    requestId: request.id,
                    expectedVersion: request.version,
                    file,
                  });
                  setFile(undefined);
                } catch (caught) {
                  setError(apiError(caught, "Attachment could not be uploaded."));
                }
              }}
            >
              Upload attachment
            </button>
          </div>
        )}
        {error && <p className="px-4 pb-4 text-sm text-rose-600">{error}</p>}
      </section>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs text-slate-500">{label}</span>
      {children}
    </label>
  );
}

function Value({ children }: { children: React.ReactNode }) {
  return <div className="input bg-slate-50 text-slate-700">{children}</div>;
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
