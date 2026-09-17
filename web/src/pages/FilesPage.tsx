import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Archive,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronsUpDown,
  Search,
} from "lucide-react";
import { useBoxes, useFiles, useWarehouses } from "@/api/hooks";
import {
  ALL_BOX_STATUSES,
  type FileActivity,
  type FileSortField,
  type TrackedFile,
} from "@/api/types";
import { LotPicker } from "@/components/LotPicker";
import { PalletPicker } from "@/components/PalletPicker";
import { useHasRole } from "@/components/RoleGate";
import { STATUS_LABEL, StatusBadge } from "@/components/StatusBadge";
import { parseFileSearchParams } from "@/pages/files";

export function FilesPage() {
  const [params, setParams] = useSearchParams();
  const parsed = useMemo(() => parseFileSearchParams(params), [params]);
  const { filters, page, pageSize } = parsed;
  const isAdmin = useHasRole(["admin"]);
  const effectiveFilters = useMemo(
    () =>
      isAdmin
        ? filters
        : { ...filters, include_inactive: undefined },
    [filters, isAdmin],
  );
  const files = useFiles(effectiveFilters, page, pageSize);
  const warehouses = useWarehouses(true);
  const boxes = useBoxes(
    {
      lot_id: filters.lot_id,
      pallet_id: filters.pallet_id,
      warehouse_id: filters.warehouse_id,
      sort_by: "box_number",
      sort_dir: "asc",
    },
    1,
    200,
  );
  const totalPages = Math.max(
    1,
    Math.ceil((files.data?.total ?? 0) / pageSize),
  );

  function setParam(name: string, value?: string) {
    const next = new URLSearchParams(params);
    value ? next.set(name, value) : next.delete(name);
    next.delete("page");
    setParams(next);
  }

  function toggleSort(field: FileSortField) {
    const next = new URLSearchParams(params);
    if (filters.sort_by !== field) {
      next.set("sort_by", field);
      next.set("sort_dir", "asc");
    } else if (filters.sort_dir === "asc") {
      next.set("sort_dir", "desc");
    } else {
      next.delete("sort_by");
      next.delete("sort_dir");
    }
    next.delete("page");
    setParams(next);
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Physical Files</h1>
        <p className="text-sm text-slate-500">
          First-class physical inventory contained in Boxes—not uploaded ERP
          documents.
        </p>
      </header>

      <section className="card card-pad grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <label className="block">
          <span className="text-xs text-slate-500">Search</span>
          <span className="relative block">
            <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-slate-400" />
            <input
              className="input pl-8"
              placeholder="Reference, description, or barcode"
              value={filters.search ?? ""}
              onChange={(event) => setParam("q", event.target.value)}
            />
          </span>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Warehouse</span>
          <select
            className="input"
            value={filters.warehouse_id ?? ""}
            onChange={(event) => setParam("warehouse_id", event.target.value)}
          >
            <option value="">All accessible</option>
            {warehouses.data?.map((warehouse) => (
              <option key={warehouse.id} value={warehouse.id}>
                {warehouse.name}{!warehouse.is_active ? " (archived)" : ""}
              </option>
            ))}
          </select>
        </label>
        <LotPicker
          label="Lot"
          value={
            filters.lot_id
              ? { id: filters.lot_id, name: params.get("lot_name") ?? `#${filters.lot_id}` }
              : null
          }
          onChange={(selection) => {
            const next = new URLSearchParams(params);
            next.delete("page");
            next.delete("pallet_id");
            next.delete("pallet_number");
            next.delete("box_id");
            if (selection) {
              next.set("lot_id", String(selection.id));
              next.set("lot_name", selection.name);
            } else {
              next.delete("lot_id");
              next.delete("lot_name");
            }
            setParams(next);
          }}
        />
        <PalletPicker
          label="Pallet"
          lotId={filters.lot_id}
          warehouseId={filters.warehouse_id}
          disabled={!filters.lot_id}
          value={
            filters.pallet_id
              ? {
                  id: filters.pallet_id,
                  pallet_number:
                    params.get("pallet_number") ?? `#${filters.pallet_id}`,
                }
              : null
          }
          onChange={(selection) => {
            const next = new URLSearchParams(params);
            next.delete("page");
            next.delete("box_id");
            if (selection) {
              next.set("pallet_id", String(selection.id));
              next.set("pallet_number", selection.pallet_number);
            } else {
              next.delete("pallet_id");
              next.delete("pallet_number");
            }
            setParams(next);
          }}
        />
        <label className="block">
          <span className="text-xs text-slate-500">Box</span>
          <select
            className="input"
            value={filters.box_id ?? ""}
            onChange={(event) => setParam("box_id", event.target.value)}
          >
            <option value="">All matching Boxes</option>
            {boxes.data?.items.map((box) => (
              <option key={box.id} value={box.id}>
                {box.box_number} · {box.lot}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Inherited Box status</span>
          <select
            className="input"
            value={filters.status ?? ""}
            onChange={(event) => setParam("status", event.target.value)}
          >
            <option value="">All statuses</option>
            {ALL_BOX_STATUSES.map((status) => (
              <option key={status} value={status}>{STATUS_LABEL[status]}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">File activity</span>
          <select
            className="input"
            value={filters.activity ?? "active"}
            onChange={(event) =>
              setParam(
                "activity",
                event.target.value === "active" ? undefined : event.target.value,
              )
            }
          >
            {(["active", "archived", "all"] as FileActivity[]).map((value) => (
              <option key={value} value={value}>
                {value === "all" ? "Active and archived" : value[0].toUpperCase() + value.slice(1)}
              </option>
            ))}
          </select>
        </label>
        {isAdmin && (
            <label className="flex items-center gap-2 self-end pb-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={!!filters.include_inactive}
                onChange={(event) =>
                  setParam("include_inactive", event.target.checked ? "true" : undefined)
                }
              />
              Include inherited archived hierarchy
            </label>
        )}
      </section>

      <section className="card overflow-hidden">
        {files.isError && (
          <p role="alert" className="p-6 text-sm text-rose-700">
            Physical Files could not be loaded. Check your warehouse access and
            retry.
          </p>
        )}
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <SortHeader label="Reference" field="reference" filters={filters} onToggle={toggleSort} />
                <th className="px-4 py-3">Description / barcode</th>
                <SortHeader label="Lot" field="lot" filters={filters} onToggle={toggleSort} />
                <SortHeader label="Pallet / Box" field="box" filters={filters} onToggle={toggleSort} />
                <SortHeader label="Warehouse" field="warehouse" filters={filters} onToggle={toggleSort} />
                <SortHeader label="Status" field="status" filters={filters} onToggle={toggleSort} />
                <SortHeader label="Updated" field="updated_at" filters={filters} onToggle={toggleSort} />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {files.data?.items.map((file) => <FileRow key={file.id} file={file} />)}
            </tbody>
          </table>
        </div>
        <div className="divide-y divide-slate-100 md:hidden">
          {files.data?.items.map((file) => <FileCard key={file.id} file={file} />)}
        </div>
        {files.isLoading && (
          <p role="status" className="p-8 text-center text-sm text-slate-500">
            Loading physical Files…
          </p>
        )}
        {!files.isLoading && !files.isError && files.data?.items.length === 0 && (
          <p className="p-8 text-center text-sm text-slate-500">
            No physical Files match these filters.
          </p>
        )}
        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-xs text-slate-500">
          <span>Page {page} of {totalPages} · {files.data?.total ?? 0} physical Files</span>
          <div className="flex items-center gap-2">
            <select
              aria-label="Files per page"
              className="input h-8 w-auto py-0 text-xs"
              value={pageSize}
              onChange={(event) => setParam("page_size", event.target.value)}
            >
              {[25, 50, 100, 200].map((size) => <option key={size}>{size}</option>)}
            </select>
            <button className="btn-ghost" disabled={page <= 1} onClick={() => setParam("page", String(page - 1))}>
              <ChevronLeft className="h-4 w-4" /> Prev
            </button>
            <button className="btn-ghost" disabled={page >= totalPages} onClick={() => setParam("page", String(page + 1))}>
              Next <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}

function Hierarchy({ file }: { file: TrackedFile }) {
  return (
    <div className="space-y-0.5">
      {file.pallet_id ? (
        <Link className="text-brand-700 hover:underline" to={`/pallets/${file.pallet_id}`}>
          {file.pallet ?? `#${file.pallet_id}`}
        </Link>
      ) : (
        <span className="text-amber-700">Unassigned</span>
      )}
      <div>
        <Link className="font-mono text-brand-700 hover:underline" to={`/boxes/${file.box_id}`}>
          Box {file.box}
        </Link>
        <span className="ml-1 text-xs text-slate-400">position {file.position}</span>
      </div>
    </div>
  );
}

function FileRow({ file }: { file: TrackedFile }) {
  return (
    <tr className={!file.is_active ? "bg-slate-50 text-slate-500" : "hover:bg-slate-50"}>
      <td className="px-4 py-3">
        <Link className="font-mono font-medium text-brand-700 hover:underline" to={`/files/${file.id}`}>
          {file.reference}
        </Link>
        {!file.is_active && <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-xs">Archived</span>}
      </td>
      <td className="max-w-64 px-4 py-3">
        <span className="block truncate" title={file.description ?? undefined}>{file.description || "—"}</span>
        {file.barcode && <span className="block truncate font-mono text-xs text-slate-500">{file.barcode}</span>}
      </td>
      <td className="px-4 py-3"><Link className="text-brand-700 hover:underline" to={`/lots/${file.lot_id}`}>{file.lot}</Link></td>
      <td className="px-4 py-3"><Hierarchy file={file} /></td>
      <td className="px-4 py-3">{file.warehouse}</td>
      <td className="px-4 py-3"><StatusBadge status={file.status} /></td>
      <td className="whitespace-nowrap px-4 py-3 text-slate-500">{new Date(file.updated_at).toLocaleString()}</td>
    </tr>
  );
}

function FileCard({ file }: { file: TrackedFile }) {
  return (
    <article className="space-y-2 p-4">
      <div className="flex items-start justify-between gap-2">
        <Link className="font-mono font-semibold text-brand-700" to={`/files/${file.id}`}>{file.reference}</Link>
        {file.is_active ? <StatusBadge status={file.status} /> : <span className="inline-flex items-center gap-1 rounded bg-slate-200 px-2 py-1 text-xs"><Archive className="h-3 w-3" /> Archived</span>}
      </div>
      <p className="line-clamp-2 text-sm text-slate-600">{file.description || "No description"}</p>
      <p className="text-xs text-slate-500">
        <Link className="text-brand-700" to={`/lots/${file.lot_id}`}>{file.lot}</Link> · {file.pallet ?? "Unassigned"} · <Link className="text-brand-700" to={`/boxes/${file.box_id}`}>Box {file.box}</Link>
      </p>
      <p className="text-xs text-slate-500">{file.warehouse} · inherited {STATUS_LABEL[file.status]}{file.barcode ? ` · ${file.barcode}` : ""}</p>
    </article>
  );
}

function SortHeader({
  label,
  field,
  filters,
  onToggle,
}: {
  label: string;
  field: FileSortField;
  filters: ReturnType<typeof parseFileSearchParams>["filters"];
  onToggle: (field: FileSortField) => void;
}) {
  const active = filters.sort_by === field;
  const Icon = !active ? ChevronsUpDown : filters.sort_dir === "asc" ? ChevronUp : ChevronDown;
  return (
    <th className="px-4 py-3" aria-sort={active ? (filters.sort_dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className="inline-flex items-center gap-1" onClick={() => onToggle(field)}>
        {label}<Icon className="h-3.5 w-3.5" />
      </button>
    </th>
  );
}
