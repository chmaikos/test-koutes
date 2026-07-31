import { useState } from "react";
import { Download } from "lucide-react";
import { api } from "@/api/client";
import { useWarehouses } from "@/api/hooks";
import type { BoxStatus } from "@/api/types";
import { ALL_BOX_STATUSES } from "@/api/types";
import { STATUS_LABEL } from "@/components/StatusBadge";

export function ExportsPage() {
  const warehouses = useWarehouses(true);
  const [warehouseId, setWarehouseId] = useState<number | "">("");
  const [status, setStatus] = useState<BoxStatus | "">("");
  const [receivedFrom, setReceivedFrom] = useState("");
  const [receivedTo, setReceivedTo] = useState("");
  const [downloading, setDownloading] = useState<"csv" | "xlsx" | null>(null);
  const [productivityWarehouseId, setProductivityWarehouseId] = useState<
    number | ""
  >("");
  const [reportDate, setReportDate] = useState(
    new Date().toISOString().slice(0, 10),
  );
  const [productivityDownloading, setProductivityDownloading] = useState<
    "csv" | "xlsx" | null
  >(null);

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

  async function downloadProductivity(format: "csv" | "xlsx") {
    setProductivityDownloading(format);
    try {
      const params: Record<string, string> = { report_date: reportDate };
      if (productivityWarehouseId) {
        params.warehouse_id = String(productivityWarehouseId);
      }
      const response = await api.get<Blob>(`/exports/productivity.${format}`, {
        params,
        responseType: "blob",
      });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `productivity-${reportDate}.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      console.error("[productivity-export] failed", err);
      const status = (err as { response?: { status?: number } }).response?.status;
      alert(`Export failed${status ? `: status ${status}` : ""}`);
    } finally {
      setProductivityDownloading(null);
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Exports</h1>
        <p className="text-sm text-slate-500">
          Download inventory snapshots and productivity reports.
        </p>
      </header>

      <section className="card card-pad space-y-4">
        <div>
          <h2 className="font-semibold">Box inventory</h2>
          <p className="text-xs text-slate-500">
            Download boxes matching the selected filters.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
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
                {!w.is_active ? " (archived)" : ""}
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
      </section>

      <section className="card card-pad space-y-4">
        <div>
          <h2 className="font-semibold">Productivity report</h2>
          <p className="text-xs text-slate-500">
            CSV contains employee averages. XLSX also includes daily entries
            from the rolling 90-day window ending on the report date.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block">
            <span className="text-xs text-slate-500">Warehouse</span>
            <select
              className="input"
              value={productivityWarehouseId}
              onChange={(event) =>
                setProductivityWarehouseId(
                  event.target.value ? Number(event.target.value) : "",
                )
              }
            >
              <option value="">All</option>
              {warehouses.data?.map((warehouse) => (
                <option key={warehouse.id} value={warehouse.id}>
                  {warehouse.name}
                  {!warehouse.is_active ? " (archived)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">Report date</span>
            <input
              type="date"
              className="input"
              value={reportDate}
              onChange={(event) => setReportDate(event.target.value)}
            />
          </label>
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            className="btn-primary"
            disabled={productivityDownloading !== null || !reportDate}
            onClick={() => downloadProductivity("csv")}
          >
            <Download className="h-4 w-4" />
            {productivityDownloading === "csv"
              ? "Preparing..."
              : "Download summary CSV"}
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={productivityDownloading !== null || !reportDate}
            onClick={() => downloadProductivity("xlsx")}
          >
            <Download className="h-4 w-4" />
            {productivityDownloading === "xlsx"
              ? "Preparing..."
              : "Download report XLSX"}
          </button>
        </div>
      </section>
    </div>
  );
}
