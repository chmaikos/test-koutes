import { Plus, Trash2 } from "lucide-react";
import type { FileInput } from "@/api/types";
import { validateFileItems } from "@/components/fileItems";

export function FileItemsEditor({
  value,
  onChange,
  disabled = false,
  requireAtLeastOne = false,
  label = "Physical Files",
}: {
  value: FileInput[];
  onChange: (value: FileInput[]) => void;
  disabled?: boolean;
  requireAtLeastOne?: boolean;
  label?: string;
}) {
  const error = validateFileItems(value, { requireAtLeastOne });

  function update(index: number, patch: Partial<FileInput>) {
    onChange(
      value.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    );
  }

  return (
    <fieldset className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/60 p-3">
      <legend className="px-1 text-sm font-semibold text-slate-800">{label}</legend>
      <p className="text-xs text-slate-600">
        Track the physical Files contained in this Box. These are inventory
        items, not uploaded ERP documents. Each immutable File barcode is
        generated automatically after save.
      </p>
      {value.map((item, index) => (
        <div
          key={index}
          className="grid gap-2 rounded-lg border border-slate-200 bg-white p-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_minmax(0,1fr)_auto]"
        >
          <label className="block">
            <span className="text-xs font-medium text-slate-600">
              File reference *
            </span>
            <input
              required
              className="input"
              maxLength={255}
              value={item.reference}
              disabled={disabled}
              onChange={(event) => update(index, { reference: event.target.value })}
            />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-slate-600">
              Description
            </span>
            <input
              className="input"
              maxLength={10000}
              value={item.description ?? ""}
              disabled={disabled}
              onChange={(event) =>
                update(index, { description: event.target.value })
              }
            />
          </label>
          <button
            type="button"
            className="btn-ghost self-end text-rose-600"
            aria-label={`Remove File ${index + 1}`}
            disabled={disabled}
            onClick={() => onChange(value.filter((_, itemIndex) => itemIndex !== index))}
          >
            <Trash2 className="h-4 w-4" /> Remove
          </button>
        </div>
      ))}
      {value.length === 0 && (
        <p className="rounded-md border border-dashed border-slate-300 p-3 text-sm text-slate-500">
          No physical Files added.
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          className="btn-secondary"
          disabled={disabled}
          onClick={() => onChange([...value, { reference: "", description: "" }])}
        >
          <Plus className="h-4 w-4" /> Add File
        </button>
        {error && (
          <p role="alert" className="text-sm text-rose-600">
            {error}
          </p>
        )}
      </div>
    </fieldset>
  );
}
