import { useState } from "react";
import { Download } from "lucide-react";
import { api } from "@/api/client";
import { useWarehouses } from "@/api/hooks";
import type { BoxStatus } from "@/api/types";
import { ALL_BOX_STATUSES } from "@/api/types";
import { STATUS_LABEL } from "@/components/StatusBadge";

export function ExportsPage() {
  const warehouses = useWarehouses();
  const [warehouseId, setWarehouseId] = useState<number | "">("");
  const [status, setStatus] = useState<BoxStatus | "">("");
  const [receivedFrom, setReceivedFrom] = useState("");
  const [receivedTo, setReceivedTo] = useState("");
  const [downloading, setDownloading] = useState<"csv" | "xlsx" | null>(null);

  async function download(format: "csv" | "xlsx") {
    setDownloading(format);
    try {
      // Re-uses the shared axios client, so the request interceptor adds the
      // local-admin HS256 token or the Entra access token transparently —
      // whichever the user is currently signed in with.
      const params: Record<string, string> = {};
      if (warehouseId) params.warehouse_id = String(warehouseId);
      if (status) params.status = status;
      // The <input type="date"> gives us "YYYY-MM-DD" which the user picked
      // on their local calendar. Build a half-open range [start-of-day,
      // end-of-day] in the user's timezone so picking "today" actually
      // covers boxes received today regardless of TZ offset.
      if (receivedFrom) {
        params.received_from = new Date(`${receivedFrom}T00:00:00`).toISOString();
      }
      if (receivedTo) {
        params.received_to = new Date(`${receivedTo}T23:59:59.999`).toISOString();
      }
      const resp = await api.get<Blob>(`/exports/boxes.${format}`, {
        params,
        responseType: "blob",
      });
      const url = URL.createObjectURL(resp.data);
      const a = document.createElement("a");
      a.href = url;
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      a.download = `boxes-${stamp}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Defer revocation: a.click() only schedules the download; the browser
      // fetches the blob asynchronously, so revoking synchronously would
      // invalidate the URL before the download starts.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      console.error("[exports] failed", err);
      const status = (err as { response?: { status?: number } }).response?.status;
      const detail = status ? `status ${status}` : (err as Error).message;
      alert(`Export failed: ${detail}`);
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
