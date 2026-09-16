import { useEffect, useId, useMemo, useState } from "react";
import { AlertCircle, Check, Plus, Search } from "lucide-react";
import { useCreateLot, useLotOptions } from "@/api/hooks";
import type { LotOption } from "@/api/types";
import {
  exactLotMatch,
  lotSelection,
  normalizeLotName,
  shouldOfferLotCreation,
} from "@/pages/lots";

export interface LotSelection {
  id: number;
  name: string;
}

export function LotPicker({
  value,
  onChange,
  warehouseId,
  canCreate = false,
  label = "Lot",
  disabled = false,
  nameValue,
  onNameChange,
  acceptedNameOnly = false,
}: {
  value: LotSelection | null;
  onChange: (selection: LotSelection | null) => void;
  warehouseId?: number;
  canCreate?: boolean;
  label?: string;
  disabled?: boolean;
  nameValue?: string;
  onNameChange?: (name: string) => void;
  acceptedNameOnly?: boolean;
}) {
  const listId = useId();
  const [query, setQuery] = useState(nameValue ?? value?.name ?? "");
  const [debounced, setDebounced] = useState(query);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [confirmCreate, setConfirmCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const options = useLotOptions(debounced);
  const create = useCreateLot();

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (nameValue !== undefined) setQuery(nameValue);
    else if (value) setQuery(value.name);
  }, [nameValue, value]);

  const items = options.data?.items ?? [];
  const duplicate = useMemo(() => exactLotMatch(items, query), [items, query]);
  const canOfferCreate = shouldOfferLotCreation(
    items,
    query,
    canCreate,
    warehouseId,
  );

  function select(option: LotOption) {
    onChange(lotSelection(option));
    onNameChange?.(option.name);
    setQuery(option.name);
    setOpen(false);
    setConfirmCreate(false);
    setError(null);
  }

  async function createNew() {
    if (!warehouseId) return;
    setError(null);
    try {
      const lot = await create.mutateAsync({
        name: query.trim(),
        warehouse_id: warehouseId,
      });
      onChange({ id: lot.id, name: lot.name });
      onNameChange?.(lot.name);
      setQuery(lot.name);
      setOpen(false);
      setConfirmCreate(false);
    } catch (caught) {
      const detail = (
        caught as { response?: { data?: { detail?: string } } }
      ).response?.data?.detail;
      setError(typeof detail === "string" ? detail : "Could not create the lot.");
    }
  }

  return (
    <div className="relative">
      <label className="block">
        <span className="text-xs text-slate-500">{label}</span>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-slate-400" />
          <input
            className="input pl-8"
            role="combobox"
            aria-autocomplete="list"
            aria-expanded={open}
            aria-controls={listId}
            aria-activedescendant={
              open && items[activeIndex]
                ? `${listId}-${items[activeIndex].id}`
                : undefined
            }
            autoComplete="off"
            disabled={disabled}
            value={query}
            placeholder="Search existing lots"
            onFocus={() => setOpen(true)}
            onChange={(event) => {
              setQuery(event.target.value);
              onNameChange?.(event.target.value);
              setOpen(true);
              setActiveIndex(0);
              setConfirmCreate(false);
              if (
                value &&
                normalizeLotName(event.target.value) !==
                  normalizeLotName(value.name)
              ) {
                onChange(null);
              }
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setOpen(true);
                setActiveIndex((index) =>
                  Math.min(index + 1, Math.max(0, items.length - 1)),
                );
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActiveIndex((index) => Math.max(0, index - 1));
              } else if (event.key === "Enter" && open && items[activeIndex]) {
                event.preventDefault();
                select(items[activeIndex]);
              } else if (event.key === "Escape") {
                setOpen(false);
              }
            }}
          />
          {(value || acceptedNameOnly) && (
            <Check
              className="pointer-events-none absolute right-2 top-2.5 h-4 w-4 text-emerald-600"
              aria-label={
                value
                  ? `Selected lot ${value.name}`
                  : `Accepted mapped lot ${query}`
              }
            />
          )}
        </div>
      </label>

      {open && !disabled && (
        <div
          id={listId}
          role="listbox"
          className="absolute z-30 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white p-1 shadow-lg"
        >
          {options.isLoading && (
            <p className="px-3 py-2 text-sm text-slate-500">Searching…</p>
          )}
          {options.isError && (
            <p role="alert" className="px-3 py-2 text-sm text-rose-600">
              Could not load lots. Try again.
            </p>
          )}
          {items.map((option, index) => (
            <button
              id={`${listId}-${option.id}`}
              role="option"
              aria-selected={value?.id === option.id}
              type="button"
              key={option.id}
              className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm ${
                index === activeIndex ? "bg-brand-50 text-brand-800" : ""
              }`}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => select(option)}
            >
              <span>{option.name}</span>
              {value?.id === option.id && <Check className="h-4 w-4" />}
            </button>
          ))}
          {!options.isLoading && items.length === 0 && !canOfferCreate && (
            <p className="px-3 py-2 text-sm text-slate-500">
              No matching lots.
            </p>
          )}
          {duplicate && value?.id !== duplicate.id && (
            <button
              type="button"
              className="mt-1 w-full rounded-md bg-amber-50 px-3 py-2 text-left text-xs text-amber-900"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => select(duplicate)}
            >
              This name already exists as “{duplicate.name}”. Select it.
            </button>
          )}
          {canOfferCreate && (
            <div className="mt-1 border-t border-slate-100 pt-1">
              {!confirmCreate ? (
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-brand-700 hover:bg-brand-50"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => setConfirmCreate(true)}
                >
                  <Plus className="h-4 w-4" /> Create new lot “
                  {query.trim().replace(/\s+/g, " ")}”
                </button>
              ) : (
                <div className="rounded-md bg-amber-50 p-3 text-xs text-amber-900">
                  <p className="flex gap-1.5">
                    <AlertCircle className="h-4 w-4 flex-none" />
                    Confirm this is a genuinely new lot, not a spelling variant.
                  </p>
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      className="btn-primary text-xs"
                      disabled={create.isPending}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => void createNew()}
                    >
                      {create.isPending ? "Creating…" : "Create and select"}
                    </button>
                    <button
                      type="button"
                      className="btn-ghost text-xs"
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => setConfirmCreate(false)}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="mt-1 text-xs text-rose-600">
          {error}
        </p>
      )}
    </div>
  );
}

