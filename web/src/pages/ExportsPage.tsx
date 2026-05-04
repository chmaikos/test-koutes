import { useState } from "react";
import { Download } from "lucide-react";
import { useMsal } from "@azure/msal-react";
import { acquireApiToken } from "@/auth/msal";
import { useWarehouses } from "@/api/hooks";
import type { BoxStatus } from "@/api/types";
import { ALL_BOX_STATUSES } from "@/api/types";
import { STATUS_LABEL } from "@/components/StatusBadge";

const baseURL = (import.meta.env.VITE_API_BASE_URL as string) || "/api";

export function ExportsPage() {
  const warehouses = useWarehouses();
  const { instance } = useMsal();
  const [warehouseId, setWarehouseId] = useState<number | "">("");
  const [status, setStatus] = useState<BoxStatus | "">("");
  const [receivedFrom, setReceivedFrom] = useState("");
  const [receivedTo, setReceivedTo] = useState("");
  const [downloading, setDownloading] = useState<"csv" | "xlsx" | null>(null);

  async function download(format: "csv" | "xlsx") {
    setDownloading(format);
    try {
      const account = instance.getActiveAccount() ?? instance.getAllAccounts()[0];
      if (!account) throw new Error("not signed in");
      const token = await acquireApiToken(account);
      const params = new URLSearchParams();
      if (warehouseId) params.set("warehouse_id", String(warehouseId));
      if (status) params.set("status", status);
      if (receivedFrom) params.set("received_from", new Date(receivedFrom).toISOString());
      if (receivedTo) params.set("received_to", new Date(receivedTo).toISOString());
      const url = `${baseURL}/exports/boxes.${format}?${params.toString()}`;
      const resp = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`status ${resp.status}`);
      const blob = await resp.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      a.download = `boxes-${stamp}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (err) {
      console.error("[exports] failed", err);
      alert(`Export failed: ${(err as Error).message}`);
    } finally {
      setDownloading(null);
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Exports</h1>
        <p className="text-sm text-slate-500">
          Download a snapshot of boxes that match your filters.
        </p>
      </header>

      <div className="card card-pad grid gap-4 md:grid-cols-2">
        <label className="block">
          <span className="text-xs text-slate-500">Warehouse</span>
          <select
            className="input"
            value={warehouseId}
            onChange={(e) =>
              setWarehouseId(e.target.value ? Number(e.target.value) : "")
            }
          >
            <option value="">All</option>
            {warehouses.data?.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Status</span>
          <select
            className="input"
            value={status}
            onChange={(e) => setStatus(e.target.value as BoxStatus | "")}
          >
            <option value="">All</option>
            {ALL_BOX_STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Received from</span>
          <input
            type="date"
            className="input"
            value={receivedFrom}
            onChange={(e) => setReceivedFrom(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Received to</span>
          <input
            type="date"
            className="input"
            value={receivedTo}
            onChange={(e) => setReceivedTo(e.target.value)}
          />
        </label>
      </div>

      <div className="flex gap-3">
        <button
          type="button"
          className="btn-primary"
          disabled={downloading !== null}
          onClick={() => download("csv")}
        >
          <Download className="h-4 w-4" />
          {downloading === "csv" ? "Preparing..." : "Download CSV"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={downloading !== null}
          onClick={() => download("xlsx")}
        >
          <Download className="h-4 w-4" />
          {downloading === "xlsx" ? "Preparing..." : "Download XLSX"}
        </button>
      </div>
    </div>
  );
}
