import { useState } from "react";
import { Download } from "lucide-react";
import { api } from "@/api/client";
import { useBoxes, useRequestAssignees, useWarehouses } from "@/api/hooks";
import type { BoxStatus, FileActivity, FileSortField, LotProgressState, PalletProgressState, RequestIssueSeverity } from "@/api/types";
import { ALL_BOX_STATUSES } from "@/api/types";
import { STATUS_LABEL } from "@/components/StatusBadge";
import { LotPicker, type LotSelection } from "@/components/LotPicker";
import { PalletPicker, type PalletSelection } from "@/components/PalletPicker";
import { useHasRole } from "@/components/RoleGate";

export function ExportsPage() {
  const isAdmin = useHasRole(["admin"]);
  const warehouses = useWarehouses(true);
  const [warehouseId, setWarehouseId] = useState<number | "">("");
  const [status, setStatus] = useState<BoxStatus | "">("");
  const [receivedFrom, setReceivedFrom] = useState("");
  const [receivedTo, setReceivedTo] = useState("");
  const [downloading, setDownloading] = useState<"csv" | "xlsx" | null>(null);
  const [fileSearch, setFileSearch] = useState("");
  const [fileWarehouseId, setFileWarehouseId] = useState<number | "">("");
  const [fileLot, setFileLot] = useState<LotSelection | null>(null);
  const [filePallet, setFilePallet] = useState<PalletSelection | null>(null);
  const [fileBoxId, setFileBoxId] = useState<number | "">("");
  const [fileStatus, setFileStatus] = useState<BoxStatus | "">("");
  const [fileActivity, setFileActivity] = useState<FileActivity>("active");
  const [fileIncludeInactive, setFileIncludeInactive] = useState(false);
  const [fileSort, setFileSort] = useState<FileSortField>("reference");
  const [fileDownloading, setFileDownloading] = useState<"csv" | "xlsx" | null>(null);
  const [lotSearch, setLotSearch] = useState("");
  const [lotWarehouseId, setLotWarehouseId] = useState<number | "">("");
  const [lotProgress, setLotProgress] = useState<LotProgressState | "">("");
  const [lotDownloading, setLotDownloading] = useState<"csv" | "xlsx" | null>(null);
  const [palletSearch, setPalletSearch] = useState("");
  const [palletProgress, setPalletProgress] = useState<PalletProgressState | "">("");
  const [palletDownloading, setPalletDownloading] = useState<"csv" | "xlsx" | null>(null);
  const [productivityWarehouseId, setProductivityWarehouseId] = useState<
    number | ""
  >("");
  const [reportDate, setReportDate] = useState(
    new Date().toISOString().slice(0, 10),
  );
  const [productivityDownloading, setProductivityDownloading] = useState<
    "csv" | "xlsx" | null
  >(null);
  const [requestWarehouseId, setRequestWarehouseId] = useState<number | "">("");
  const [requestAssigneeId, setRequestAssigneeId] = useState<number | "">("");
  const [requestSeverity, setRequestSeverity] = useState<
    RequestIssueSeverity | ""
  >("");
  const [requestIssueType, setRequestIssueType] = useState("");
  const [requestFrom, setRequestFrom] = useState("");
  const [requestTo, setRequestTo] = useState("");
  const [requestDownloading, setRequestDownloading] = useState<string | null>(
    null,
  );
  const requestAssignees = useRequestAssignees(
    requestWarehouseId || undefined,
  );
  const fileBoxes = useBoxes(
    {
      lot_id: fileLot?.id,
      pallet_id: filePallet?.id,
      warehouse_id: fileWarehouseId || undefined,
      sort_by: "box_number",
      sort_dir: "asc",
    },
    1,
    200,
  );

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

  async function downloadFiles(format: "csv" | "xlsx") {
    setFileDownloading(format);
    try {
      const params: Record<string, string | number | boolean> = {
        activity: fileActivity,
        include_inactive: isAdmin && fileIncludeInactive,
        sort_by: fileSort,
        sort_dir: "asc",
      };
      if (fileSearch.trim()) params.search = fileSearch.trim();
      if (fileWarehouseId) params.warehouse_id = fileWarehouseId;
      if (fileLot) params.lot_id = fileLot.id;
      if (filePallet) params.pallet_id = filePallet.id;
      if (fileBoxId) params.box_id = fileBoxId;
      if (fileStatus) params.status = fileStatus;
      const response = await api.get<Blob>(`/exports/files.${format}`, {
        params,
        responseType: "blob",
      });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `physical-files-${new Date().toISOString().replace(/[:.]/g, "-")}.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      const status = (err as { response?: { status?: number } }).response?.status;
      alert(`File export failed${status ? `: status ${status}` : ""}`);
    } finally {
      setFileDownloading(null);
    }
  }

  async function downloadLots(format: "csv" | "xlsx") {
    setLotDownloading(format);
    try {
      const params: Record<string, string> = {};
      if (lotSearch.trim()) params.search = lotSearch.trim();
      if (lotWarehouseId) params.warehouse_id = String(lotWarehouseId);
      if (lotProgress) params.progress_state = lotProgress;
      const response = await api.get<Blob>(`/exports/lots.${format}`, {
        params,
        responseType: "blob",
      });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `lots-${new Date().toISOString().replace(/[:.]/g, "-")}.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      const status = (err as { response?: { status?: number } }).response?.status;
      alert(`Lot export failed${status ? `: status ${status}` : ""}`);
    } finally {
      setLotDownloading(null);
    }
  }

  async function downloadPallets(format: "csv" | "xlsx") {
    setPalletDownloading(format);
    try {
      const params: Record<string, string> = {};
      if (palletSearch.trim()) params.search = palletSearch.trim();
      if (palletProgress) params.progress_state = palletProgress;
      const response = await api.get<Blob>(`/exports/pallets.${format}`, {
        params,
        responseType: "blob",
      });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `pallets-${new Date().toISOString().replace(/[:.]/g, "-")}.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } finally {
      setPalletDownloading(null);
    }
  }

  async function downloadRequestReport(
    report: "request-reconciliation" | "request-analytics",
    format: "csv" | "xlsx",
  ) {
    const key = `${report}-${format}`;
    setRequestDownloading(key);
    try {
      const params: Record<string, string> = {};
      if (requestWarehouseId) {
        params.warehouse_id = String(requestWarehouseId);
      }
      if (requestAssigneeId) {
        params.assigned_mover_user_id = String(requestAssigneeId);
      }
      if (requestFrom) {
        params.from_at = new Date(`${requestFrom}T00:00:00`).toISOString();
      }
      if (requestTo) {
        const exclusiveEnd = new Date(`${requestTo}T00:00:00`);
        exclusiveEnd.setDate(exclusiveEnd.getDate() + 1);
        params.to_at = exclusiveEnd.toISOString();
      }
      if (report === "request-reconciliation" && requestSeverity) {
        params.severity = requestSeverity;
      }
      if (report === "request-reconciliation" && requestIssueType) {
        params.issue_type = requestIssueType;
      }
      const response = await api.get<Blob>(`/exports/${report}.${format}`, {
        params,
        responseType: "blob",
      });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      anchor.download = `${report}-${stamp}.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      console.error("[request-export] failed", err);
      const status = (err as { response?: { status?: number } }).response?.status;
      alert(`Export failed${status ? `: status ${status}` : ""}`);
    } finally {
      setRequestDownloading(null);
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
            Box-granular rows with lot, pallet, status, physical File count and
            File summary matching the selected filters. Generated barcode
            columns are immutable identifiers.
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
          <h2 className="font-semibold">Physical Files</h2>
          <p className="text-xs text-slate-500">
            Export one row per tracked physical File. This does not export
            uploaded ERP documents. The generated barcode column is immutable.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <label className="block"><span className="text-xs text-slate-500">Search</span><input className="input" placeholder="Reference, description, barcode" value={fileSearch} onChange={(event) => setFileSearch(event.target.value)} /></label>
          <label className="block"><span className="text-xs text-slate-500">Warehouse</span><select className="input" value={fileWarehouseId} onChange={(event) => setFileWarehouseId(event.target.value ? Number(event.target.value) : "")}><option value="">All accessible</option>{warehouses.data?.map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name}</option>)}</select></label>
          <LotPicker label="Lot" value={fileLot} onChange={(lot) => { setFileLot(lot); setFilePallet(null); setFileBoxId(""); }} warehouseId={fileWarehouseId || undefined} />
          <PalletPicker label="Pallet" value={filePallet} onChange={(pallet) => { setFilePallet(pallet); setFileBoxId(""); }} lotId={fileLot?.id} warehouseId={fileWarehouseId || undefined} disabled={!fileLot} />
          <label className="block"><span className="text-xs text-slate-500">Box</span><select className="input" value={fileBoxId} onChange={(event) => setFileBoxId(event.target.value ? Number(event.target.value) : "")}><option value="">All matching Boxes</option>{fileBoxes.data?.items.map((box) => <option key={box.id} value={box.id}>{box.box_number} · {box.lot}</option>)}</select></label>
          <label className="block"><span className="text-xs text-slate-500">Inherited status</span><select className="input" value={fileStatus} onChange={(event) => setFileStatus(event.target.value as BoxStatus | "")}><option value="">All</option>{ALL_BOX_STATUSES.map((status) => <option key={status} value={status}>{STATUS_LABEL[status]}</option>)}</select></label>
          <label className="block"><span className="text-xs text-slate-500">File activity</span><select className="input" value={fileActivity} onChange={(event) => setFileActivity(event.target.value as FileActivity)}><option value="active">Active</option><option value="archived">Archived</option><option value="all">Active and archived</option></select></label>
          <label className="block"><span className="text-xs text-slate-500">Sort by</span><select className="input" value={fileSort} onChange={(event) => setFileSort(event.target.value as FileSortField)}>{["reference", "lot", "pallet", "box", "warehouse", "status", "position", "created_at", "updated_at"].map((field) => <option key={field} value={field}>{field.replaceAll("_", " ")}</option>)}</select></label>
        </div>
        {isAdmin && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={fileIncludeInactive} onChange={(event) => setFileIncludeInactive(event.target.checked)} />Include Files inherited from archived Boxes, Lots, pallets, or warehouses</label>}
        <div className="flex flex-wrap gap-3">
          <button className="btn-primary" disabled={fileDownloading !== null} onClick={() => void downloadFiles("csv")}><Download className="h-4 w-4" />{fileDownloading === "csv" ? "Preparing…" : "Download Files CSV"}</button>
          <button className="btn-secondary" disabled={fileDownloading !== null} onClick={() => void downloadFiles("xlsx")}><Download className="h-4 w-4" />{fileDownloading === "xlsx" ? "Preparing…" : "Download Files XLSX"}</button>
        </div>
      </section>

      <section className="card card-pad space-y-4">
        <div>
          <h2 className="font-semibold">Pallet summary</h2>
          <p className="text-xs text-slate-500">
            Export ACL-scoped pallet organization across box warehouses,
            including generated immutable barcodes, lots, box counts, status
            distribution, completion, and latest activity.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block"><span className="text-xs text-slate-500">Search</span><input className="input" placeholder="Pallet number, lot, or barcode" value={palletSearch} onChange={(event) => setPalletSearch(event.target.value)} /></label>
          <label className="block"><span className="text-xs text-slate-500">Progress</span><select className="input" value={palletProgress} onChange={(event) => setPalletProgress(event.target.value as PalletProgressState | "")}><option value="">All</option><option value="active">Active</option><option value="in_progress">In progress</option><option value="complete">Complete</option><option value="no_eligible">No eligible boxes</option></select></label>
        </div>
        <div className="flex flex-wrap gap-3">
          <button className="btn-primary" disabled={palletDownloading !== null} onClick={() => void downloadPallets("csv")}><Download className="h-4 w-4" />{palletDownloading === "csv" ? "Preparing…" : "Download pallets CSV"}</button>
          <button className="btn-secondary" disabled={palletDownloading !== null} onClick={() => void downloadPallets("xlsx")}><Download className="h-4 w-4" />{palletDownloading === "xlsx" ? "Preparing…" : "Download pallets XLSX"}</button>
        </div>
      </section>

      <section className="card card-pad space-y-4">
        <div>
          <h2 className="font-semibold">Lot summary</h2>
          <p className="text-xs text-slate-500">
            Export ACL-scoped generated immutable barcodes, lot counts,
            completion, status distribution, warehouses, staged receipts, and
            last activity.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          <label className="block">
            <span className="text-xs text-slate-500">Search</span>
            <input
              className="input"
              placeholder="Lot name or barcode"
              value={lotSearch}
              onChange={(event) => setLotSearch(event.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">Warehouse</span>
            <select
              className="input"
              value={lotWarehouseId}
              onChange={(event) =>
                setLotWarehouseId(
                  event.target.value ? Number(event.target.value) : "",
                )
              }
            >
              <option value="">All accessible</option>
              {warehouses.data?.map((warehouse) => (
                <option key={warehouse.id} value={warehouse.id}>
                  {warehouse.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">Progress</span>
            <select
              className="input"
              value={lotProgress}
              onChange={(event) =>
                setLotProgress(event.target.value as LotProgressState | "")
              }
            >
              <option value="">All</option>
              <option value="active">Active</option>
              <option value="in_progress">In progress</option>
              <option value="complete">Complete</option>
              <option value="no_eligible">No eligible boxes</option>
            </select>
          </label>
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            className="btn-primary"
            disabled={lotDownloading !== null}
            onClick={() => void downloadLots("csv")}
          >
            <Download className="h-4 w-4" />
            {lotDownloading === "csv" ? "Preparing…" : "Download lots CSV"}
          </button>
          <button
            className="btn-secondary"
            disabled={lotDownloading !== null}
            onClick={() => void downloadLots("xlsx")}
          >
            <Download className="h-4 w-4" />
            {lotDownloading === "xlsx" ? "Preparing…" : "Download lots XLSX"}
          </button>
        </div>
      </section>

      <section className="card card-pad space-y-4">
        <div>
          <h2 className="font-semibold">Request reconciliation and analytics</h2>
          <p className="text-xs text-slate-500">
            Export the same ACL-scoped issues and metrics shown on the
            reconciliation dashboard, including pallet context where present.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-6">
          <label className="block">
            <span className="text-xs text-slate-500">Warehouse</span>
            <select
              className="input"
              value={requestWarehouseId}
              onChange={(event) => {
                setRequestWarehouseId(
                  event.target.value ? Number(event.target.value) : "",
                );
                setRequestAssigneeId("");
              }}
            >
              <option value="">All accessible</option>
              {warehouses.data?.map((warehouse) => (
                <option key={warehouse.id} value={warehouse.id}>
                  {warehouse.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">
              Reconciliation issue
            </span>
            <select
              className="input"
              value={requestIssueType}
              onChange={(event) => setRequestIssueType(event.target.value)}
            >
              <option value="">All</option>
              {[
                "shortage",
                "excess",
                "typed_discrepancy",
                "open_follow_up",
                "missing_erp_document",
                "superseded_erp_document",
                "admin_override",
                "cancelled_reservation",
                "awaiting_confirmation",
                "operational_exception",
                "sla_breach",
                "review_pending_self_receipt",
              ].map((value) => (
                <option key={value} value={value}>
                  {value.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">Assignee</span>
            <select
              className="input"
              value={requestAssigneeId}
              disabled={!requestWarehouseId}
              onChange={(event) =>
                setRequestAssigneeId(
                  event.target.value ? Number(event.target.value) : "",
                )
              }
            >
              <option value="">
                {requestWarehouseId
                  ? "All assignees"
                  : "Choose warehouse first"}
              </option>
              {requestAssignees.data?.map((assignee) => (
                <option key={assignee.id} value={assignee.id}>
                  {assignee.display_name}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">
              Reconciliation severity
            </span>
            <select
              className="input"
              value={requestSeverity}
              onChange={(event) =>
                setRequestSeverity(
                  event.target.value as RequestIssueSeverity | "",
                )
              }
            >
              <option value="">All</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">From</span>
            <input
              type="date"
              className="input"
              value={requestFrom}
              onChange={(event) => setRequestFrom(event.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">Through</span>
            <input
              type="date"
              className="input"
              value={requestTo}
              onChange={(event) => setRequestTo(event.target.value)}
            />
          </label>
        </div>
        <div className="flex flex-wrap gap-3">
          {(
            [
              ["request-reconciliation", "csv", "Reconciliation CSV"],
              ["request-reconciliation", "xlsx", "Reconciliation XLSX"],
              ["request-analytics", "csv", "Analytics CSV"],
              ["request-analytics", "xlsx", "Analytics XLSX"],
            ] as const
          ).map(([report, format, label]) => {
            const key = `${report}-${format}`;
            return (
              <button
                key={key}
                type="button"
                className={
                  format === "csv" ? "btn-primary" : "btn-secondary"
                }
                disabled={requestDownloading !== null}
                onClick={() => downloadRequestReport(report, format)}
              >
                <Download className="h-4 w-4" />
                {requestDownloading === key ? "Preparing..." : label}
              </button>
            );
          })}
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
