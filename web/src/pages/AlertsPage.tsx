import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Clock } from "lucide-react";
import {
  useAcknowledgeAlert,
  useAlerts,
  useWarehouses,
} from "@/api/hooks";
import type { AlertType } from "@/api/types";
import { useHasRole } from "@/components/RoleGate";

interface TypeStyle {
  label: string;
  icon: typeof AlertTriangle;
  iconClass: string;
}

const ALERT_TYPE_STYLES: Record<AlertType, TypeStyle> = {
  low_inventory: {
    label: "Low inventory",
    icon: AlertTriangle,
    iconClass: "text-rose-500",
  },
  max_capacity: {
    label: "Max capacity",
    icon: AlertTriangle,
    iconClass: "text-rose-500",
  },
  near_capacity: {
    label: "Near capacity",
    icon: AlertTriangle,
    iconClass: "text-amber-500",
  },
  near_low_inventory: {
    label: "Near low inventory",
    icon: AlertTriangle,
    iconClass: "text-amber-500",
  },
  box_stuck: {
    label: "Boxes stuck",
    icon: Clock,
    iconClass: "text-sky-600",
  },
};

function formatValueThreshold(type: AlertType, value: number, threshold: number): string {
  if (type === "box_stuck") {
    return `${value} stuck (>${threshold}d)`;
  }
  return `${value} / ${threshold}`;
}

export function AlertsPage() {
  const [onlyOpen, setOnlyOpen] = useState(true);
  const { data, isLoading } = useAlerts(onlyOpen);
  const ack = useAcknowledgeAlert();
  const warehouses = useWarehouses();
  const canWrite = useHasRole(["admin", "operator"]);

  return (
    <div className="space-y-5">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Alerts</h1>
          <p className="text-sm text-slate-500">
            Inventory thresholds, leading indicators, and stuck-box checks.
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
                    {formatValueThreshold(a.type, a.value, a.threshold)}
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">
                    {new Date(a.triggered_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5">
                    {a.resolved_at ? (
                      <span className="badge bg-emerald-100 text-emerald-700">
                        <CheckCircle2 className="h-3 w-3" /> Resolved
                      </span>
                    ) : a.escalated_at ? (
                      <span className="badge bg-rose-100 text-rose-700">
                        Escalated
                      </span>
                    ) : a.acknowledged_at ? (
                      <span className="badge bg-slate-200 text-slate-700">
                        Acknowledged
                      </span>
                    ) : (
                      <span className="badge bg-amber-100 text-amber-700">Open</span>
                    )}
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
    </div>
  );
}
