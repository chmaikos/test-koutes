import { useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2 } from "lucide-react";
import {
  useAcknowledgeAlert,
  useAlerts,
  useWarehouses,
} from "@/api/hooks";
import { useHasRole } from "@/components/RoleGate";
import {
  ALERT_TYPE_STYLES,
  formatAlertValueThreshold,
} from "@/pages/alertPresentation";

export function AlertsPage() {
  const [onlyOpen, setOnlyOpen] = useState(true);
  const { data, isLoading } = useAlerts(onlyOpen);
  const ack = useAcknowledgeAlert();
  const warehouses = useWarehouses(true);
  const canWrite = useHasRole(["admin", "operator"]);

  return (
    <div className="space-y-5">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Alerts</h1>
          <p className="text-sm text-slate-500">
            Inventory thresholds and leading indicators.
          </p>
        </div>
        <label className="inline-flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={onlyOpen}
            onChange={(e) => setOnlyOpen(e.target.checked)}
          />
          Open only
        </label>
      </header>

      <div className="card overflow-hidden">
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-4 py-2.5 text-left">Warehouse</th>
                <th className="px-4 py-2.5 text-left">Type</th>
                <th className="px-4 py-2.5 text-left">Value / threshold</th>
                <th className="px-4 py-2.5 text-left">Triggered</th>
                <th className="px-4 py-2.5 text-left">State</th>
                <th className="px-4 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {isLoading && (
                <tr>
                  <td className="px-4 py-8 text-center text-slate-400" colSpan={6}>
                    Loading...
                  </td>
                </tr>
              )}
              {!isLoading && data?.length === 0 && (
                <tr>
                  <td className="px-4 py-8 text-center text-slate-400" colSpan={6}>
                    No alerts.
                  </td>
                </tr>
              )}
              {data?.map((a) => {
                const wh = warehouses.data?.find((w) => w.id === a.warehouse_id);
                const style =
                  ALERT_TYPE_STYLES[a.type] ?? ALERT_TYPE_STYLES.low_inventory;
                const Icon = style.icon;
                return (
                  <tr key={a.id} className="hover:bg-slate-50">
                    <td className="px-4 py-2.5">
                      <Link
                        to={`/alerts/${a.id}`}
                        className="font-medium text-slate-700 hover:text-sky-600 hover:underline"
                      >
                        {wh?.name ?? `#${a.warehouse_id}`}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="inline-flex items-center gap-1.5">
                        <Icon className={`h-4 w-4 ${style.iconClass}`} />
                        {style.label}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      {formatAlertValueThreshold(a.type, a.value, a.threshold)}
                    </td>
                    <td className="px-4 py-2.5 text-slate-500">
                      {new Date(a.triggered_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-2.5">
                      <AlertStateBadge alert={a} />
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {canWrite && !a.acknowledged_at && !a.resolved_at && (
                        <button
                          type="button"
                          className="btn-secondary text-xs"
                          disabled={ack.isPending}
                          onClick={() => ack.mutate(a.id)}
                        >
                          Acknowledge
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="divide-y divide-slate-100 md:hidden">
          {isLoading && (
            <div className="px-4 py-8 text-center text-slate-400">
              Loading...
            </div>
          )}
          {!isLoading && data?.length === 0 && (
            <div className="px-4 py-8 text-center text-slate-400">No alerts.</div>
          )}
          {data?.map((a) => {
            const wh = warehouses.data?.find((w) => w.id === a.warehouse_id);
            const style =
              ALERT_TYPE_STYLES[a.type] ?? ALERT_TYPE_STYLES.low_inventory;
            const Icon = style.icon;
            return (
              <div key={a.id} className="px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 text-sm font-medium text-slate-800">
                      <Icon className={`h-4 w-4 ${style.iconClass}`} />
                      {style.label}
                    </div>
                    <Link
                      to={`/alerts/${a.id}`}
                      className="mt-0.5 block truncate text-sm text-slate-600 hover:text-sky-600 hover:underline"
                    >
                      {wh?.name ?? `#${a.warehouse_id}`}
                    </Link>
                  </div>
                  <AlertStateBadge alert={a} />
                </div>
                <dl className="mt-2 grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
                  <dt className="text-slate-500">Value / limit</dt>
                  <dd className="text-slate-800">
                    {formatAlertValueThreshold(a.type, a.value, a.threshold)}
                  </dd>
                  <dt className="text-slate-500">Triggered</dt>
                  <dd className="text-slate-500">
                    {new Date(a.triggered_at).toLocaleString()}
                  </dd>
                </dl>
                {canWrite && !a.acknowledged_at && !a.resolved_at && (
                  <button
                    type="button"
                    className="btn-secondary mt-3 w-full"
                    disabled={ack.isPending}
                    onClick={() => ack.mutate(a.id)}
                  >
                    Acknowledge
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function AlertStateBadge({
  alert,
}: {
  alert: import("@/api/types").Alert;
}) {
  if (alert.resolved_at) {
    return (
      <span className="badge bg-emerald-100 text-emerald-700">
        <CheckCircle2 className="h-3 w-3" /> Resolved
      </span>
    );
  }
  if (alert.acknowledged_at) {
    return (
      <span className="badge bg-slate-200 text-slate-700">Acknowledged</span>
    );
  }
  return <span className="badge bg-amber-100 text-amber-700">Open</span>;
}
