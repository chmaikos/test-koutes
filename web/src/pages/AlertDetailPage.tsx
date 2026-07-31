import { Link, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  Bell,
  CheckCircle2,
  Clock,
  Mail,
  Send,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import {
  useAcknowledgeAlert,
  useAlert,
  useSendTestAlertEmail,
  useWarehouses,
} from "@/api/hooks";
import type {
  Alert,
  AlertNotification,
  AlertNotificationKind,
  AlertType,
} from "@/api/types";
import { useHasRole } from "@/components/RoleGate";

const ALERT_TYPE_LABEL: Record<AlertType, string> = {
  low_inventory: "Low inventory",
  max_capacity: "Max capacity",
  near_capacity: "Near capacity",
  near_low_inventory: "Near low inventory",
  box_stuck: "Boxes stuck",
};

const NOTIFICATION_KIND_LABEL: Record<AlertNotificationKind, string> = {
  triggered: "Triggered email",
  reminder: "Reminder email",
  escalated: "Escalated to admins",
  resolved: "Resolved email",
  test: "Test email",
};

const NOTIFICATION_KIND_ICON: Record<
  AlertNotificationKind,
  typeof Bell
> = {
  triggered: AlertTriangle,
  reminder: Clock,
  escalated: ShieldAlert,
  resolved: CheckCircle2,
  test: Mail,
};

export function AlertDetailPage() {
  const { id } = useParams<{ id: string }>();
  const alertId = id ? Number(id) : undefined;
  const { data, isLoading, error } = useAlert(alertId);
  const warehouses = useWarehouses(true);
  const ack = useAcknowledgeAlert();
  const sendTest = useSendTestAlertEmail();
  const canWrite = useHasRole(["admin", "operator"]);
  const isAdmin = useHasRole(["admin"]);
  const [testResultMsg, setTestResultMsg] = useState<string | null>(null);

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading…</p>;
  }
  if (error || !data) {
    return (
      <div className="space-y-3">
        <Link
          to="/alerts"
          className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
        >
          <ArrowLeft className="h-4 w-4" /> All alerts
        </Link>
        <p className="text-sm text-rose-600">Alert not found.</p>
      </div>
    );
  }

  const { alert, notifications } = data;
  const wh = warehouses.data?.find((w) => w.id === alert.warehouse_id);
  const warehouseName = wh?.name ?? `#${alert.warehouse_id}`;
  const stateBadge = renderStateBadge(alert);

  return (
    <div className="space-y-5">
      <Link
        to="/alerts"
        className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
      >
        <ArrowLeft className="h-4 w-4" /> All alerts
      </Link>

      <header className="card card-pad space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              {ALERT_TYPE_LABEL[alert.type] ?? alert.type}
            </h1>
            <p className="text-sm text-slate-500">
              <Link to="/" className="hover:text-slate-700 hover:underline">
                {warehouseName}
              </Link>
              {wh && (
                <>
                  {" · "}
                  <span>
                    capacity {wh.min_inventory}–{wh.max_capacity}
                  </span>
                </>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">{stateBadge}</div>
        </div>

        <InventoryBar alert={alert} />

        <div className="grid gap-3 text-sm sm:grid-cols-2">
          <Field label="Triggered">
            {new Date(alert.triggered_at).toLocaleString()}
          </Field>
          <Field label="First emailed">
            {alert.notified_at
              ? new Date(alert.notified_at).toLocaleString()
              : "Never"}
          </Field>
          <Field label="Acknowledged">
            {alert.acknowledged_at
              ? new Date(alert.acknowledged_at).toLocaleString()
              : "—"}
          </Field>
          <Field label="Escalated">
            {alert.escalated_at
              ? new Date(alert.escalated_at).toLocaleString()
              : "—"}
          </Field>
          <Field label="Resolved">
            {alert.resolved_at
              ? new Date(alert.resolved_at).toLocaleString()
              : "—"}
          </Field>
          <Field label="Value vs threshold">
            {formatValueThreshold(alert)}
          </Field>
        </div>

        <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-3">
          {canWrite && !alert.acknowledged_at && !alert.resolved_at && (
            <button
              type="button"
              className="btn-secondary text-sm"
              disabled={ack.isPending}
              onClick={() => ack.mutate(alert.id)}
            >
              Acknowledge
            </button>
          )}
          {isAdmin && (
            <button
              type="button"
              className="btn-secondary text-sm"
              disabled={sendTest.isPending}
              onClick={async () => {
                setTestResultMsg(null);
                try {
                  const r = await sendTest.mutateAsync(alert.id);
                  if (r.ok) {
                    setTestResultMsg(
                      `Test email sent to ${r.recipients.join(", ")}.`,
                    );
                  } else {
                    setTestResultMsg(
                      r.error ? `Test email failed: ${r.error}` : "Test email failed.",
                    );
                  }
                } catch {
                  setTestResultMsg("Test email failed.");
                }
              }}
            >
              <Send className="mr-1 inline h-3.5 w-3.5" />
              Send test email to me
            </button>
          )}
          {testResultMsg && (
            <span className="text-xs text-slate-500">{testResultMsg}</span>
          )}
        </div>
      </header>

      <section className="card overflow-hidden">
        <header className="border-b border-slate-100 px-5 py-3">
          <h2 className="font-semibold">Notification log</h2>
          <p className="text-xs text-slate-500">
            Every email attempt for this alert. Failed sends are kept so the
            dispatcher can retry and so the failure reason is auditable.
          </p>
        </header>
        {notifications.length === 0 ? (
          <p className="px-5 py-6 text-sm text-slate-400">
            No emails have been sent for this alert yet.
          </p>
        ) : (
          <ol className="divide-y divide-slate-100">
            {notifications.map((n) => (
              <NotificationRow key={n.id} notification={n} />
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

function renderStateBadge(alert: Alert) {
  if (alert.resolved_at) {
    return (
      <span className="badge bg-emerald-100 text-emerald-700">
        <CheckCircle2 className="h-3 w-3" /> Resolved
      </span>
    );
  }
  if (alert.escalated_at) {
    return (
      <span className="badge bg-rose-100 text-rose-700">
        <ShieldAlert className="h-3 w-3" /> Escalated
      </span>
    );
  }
  if (alert.acknowledged_at) {
    return (
      <span className="badge bg-slate-200 text-slate-700">Acknowledged</span>
    );
  }
  return (
    <span className="badge bg-amber-100 text-amber-700">
      <AlertTriangle className="h-3 w-3" /> Open
    </span>
  );
}

function formatValueThreshold(alert: Alert): string {
  if (alert.type === "box_stuck") {
    return `${alert.value} stuck box(es); threshold ${alert.threshold} day(s)`;
  }
  return `${alert.value} / ${alert.threshold}`;
}

function InventoryBar({ alert }: { alert: Alert }) {
  const pct = (() => {
    if (alert.threshold <= 0) {
      return alert.value > 0 ? 100 : 0;
    }
    return Math.max(0, Math.min(100, Math.round((alert.value * 100) / alert.threshold)));
  })();
  // For low-inventory alerts the "filled" portion is ironically reassuring,
  // for max-capacity it's the alarm. Pick a colour accordingly so the bar
  // is intuitive at a glance.
  const colour = (() => {
    switch (alert.type) {
      case "max_capacity":
        return "bg-rose-500";
      case "near_capacity":
        return "bg-amber-500";
      case "low_inventory":
        return "bg-rose-400";
      case "near_low_inventory":
        return "bg-amber-400";
      case "box_stuck":
        return "bg-sky-500";
      default:
        return "bg-slate-400";
    }
  })();
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between text-xs text-slate-500">
        <span>Value</span>
        <span>
          {alert.value} / {alert.threshold} ({pct}%)
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full ${colour}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function NotificationRow({ notification }: { notification: AlertNotification }) {
  const Icon = NOTIFICATION_KIND_ICON[notification.kind] ?? Bell;
  const recipients = notification.recipients
    ? notification.recipients.split(",").map((s) => s.trim()).filter(Boolean)
    : [];
  return (
    <li className="flex gap-3 px-5 py-3">
      <div
        className={`mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-full ${
          notification.ok
            ? "bg-emerald-50 text-emerald-600"
            : "bg-rose-50 text-rose-600"
        }`}
      >
        {notification.ok ? (
          <Icon className="h-4 w-4" />
        ) : (
          <XCircle className="h-4 w-4" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span className="font-medium text-slate-700">
            {NOTIFICATION_KIND_LABEL[notification.kind] ?? notification.kind}
          </span>
          <span className="text-xs text-slate-500">
            {new Date(notification.sent_at).toLocaleString()}
          </span>
          {!notification.ok && (
            <span className="badge bg-rose-100 text-rose-700">Failed</span>
          )}
        </div>
        {recipients.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {recipients.map((r) => (
              <span
                key={r}
                className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700"
              >
                {r}
              </span>
            ))}
          </div>
        )}
        {notification.error && (
          <p className="mt-1 text-xs italic text-rose-600">
            {notification.error}
          </p>
        )}
      </div>
    </li>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wider text-slate-400">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}
