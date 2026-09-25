import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, Plus, Search } from "lucide-react";
import { useCreatePallet, usePalletOptions } from "@/api/hooks";
import type { PalletOption } from "@/api/types";
import { useHasRole } from "@/components/RoleGate";
import {
  exactPalletMatch,
  normalizePalletNumber,
  palletPickerOptionFilters,
  palletScopeKey,
  palletWarehouseDistributionLabel,
} from "@/pages/pallets";

export interface PalletSelection {
  id: number;
  pallet_number: string;
}

export function PalletPicker({
  value,
  onChange,
  lotId,
  warehouseId,
  canCreate = false,
  required = false,
  disabled = false,
  label = "Pallet",
  numberValue,
  onNumberChange,
  acceptedNumberOnly = false,
}: {
  value: PalletSelection | null;
  onChange: (value: PalletSelection | null) => void;
  lotId?: number;
  warehouseId?: number;
  canCreate?: boolean;
  required?: boolean;
  disabled?: boolean;
  label?: string;
  numberValue?: string | null;
  onNumberChange?: (value: string) => void;
  acceptedNumberOnly?: boolean;
}) {
  const listId = useId();
  const [query, setQuery] = useState(
    numberValue ?? value?.pallet_number ?? "",
  );
  const [debounced, setDebounced] = useState(query);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isAdmin = useHasRole(["admin"]);
  const scope = palletScopeKey(lotId);
  const previousScope = useRef(scope);
  const options = usePalletOptions(
    palletPickerOptionFilters(lotId, debounced, isAdmin),
    1,
    50,
  );
  const create = useCreatePallet();
  const items = options.data?.items ?? [];
  const exact = useMemo(() => exactPalletMatch(items, query), [items, query]);
  const canOfferCreate =
    canCreate &&
    !!lotId &&
    !!warehouseId &&
    !!query.trim() &&
    !exact;

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query), 200);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    setQuery(numberValue ?? value?.pallet_number ?? "");
  }, [numberValue, value]);

  useEffect(() => {
    if (previousScope.current === scope) return;
    previousScope.current = scope;
    const preservedMappedNumber =
      acceptedNumberOnly && numberValue?.trim() ? numberValue : "";
    setQuery(preservedMappedNumber);
    setDebounced(preservedMappedNumber);
    setOpen(false);
    setError(null);
    onChange(null);
    if (!preservedMappedNumber) onNumberChange?.("");
    // Pallet identity is scoped by lot. A receipt warehouse change must not
    // clear a valid pallet selection, and accepting a mapped lot must not
    // clear its spreadsheet-mapped pallet number.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  function select(option: PalletOption) {
    if (!option.is_active) {
      setError(
        "This pallet is archived. Ask an administrator to restore it before use.",
      );
      return;
    }
    const selected = {
      id: option.id,
      pallet_number: option.pallet_number,
    };
    setQuery(option.pallet_number);
    onNumberChange?.(option.pallet_number);
    onChange(selected);
    setOpen(false);
    setError(null);
  }

  function leaveUnassigned() {
    setQuery("");
    setDebounced("");
    onNumberChange?.("");
    onChange(null);
    setOpen(false);
    setError(null);
  }

  async function createAndSelect() {
    if (!lotId || !warehouseId || !query.trim()) return;
    setError(null);
    try {
      const pallet = await create.mutateAsync({
        lot_id: lotId,
        warehouse_id: warehouseId,
        pallet_number: query.trim(),
      });
      setQuery(pallet.pallet_number);
      onNumberChange?.(pallet.pallet_number);
      onChange({ id: pallet.id, pallet_number: pallet.pallet_number });
      setOpen(false);
    } catch (caught) {
      const detail = (
        caught as {
          response?: {
            data?: { detail?: string | { code?: string; message?: string } };
          };
        }
      ).response?.data?.detail;
      const message =
        typeof detail === "object" && detail?.code
          ? detail.code.includes("inactive")
            ? "An archived pallet has this number. Ask an administrator to restore it."
            : detail.message || "This pallet number conflicts with an existing pallet."
          : typeof detail === "string"
            ? detail
            : "The pallet could not be created.";
      setError(
        `${message} The receipt warehouse is used only to authorize creation; pallets do not have a warehouse location.`,
      );
    }
  }

  const hasUnscopedNumber = !lotId && !!numberValue?.trim();
  const unavailable = disabled || (!lotId && !hasUnscopedNumber);
  return (
    <div className="relative">
      <label className="block">
        <span className="text-xs text-slate-500">{label}</span>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-slate-400" />
          <input
            className="input pl-8"
            required={required}
            disabled={unavailable}
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            autoComplete="off"
            placeholder={
              !lotId && !hasUnscopedNumber
                ? "Choose lot first"
                : required
                  ? "Search pallet number or generated barcode"
                  : "Optional pallet number/barcode — blank is unassigned"
            }
            value={query}
            onFocus={() => {
              if (lotId) setOpen(true);
            }}
            onChange={(event) => {
              const next = event.target.value;
              setQuery(next);
              onNumberChange?.(next);
              setOpen(!!lotId);
              setError(null);
              if (
                value &&
                normalizePalletNumber(next) !==
                  normalizePalletNumber(value.pallet_number)
              ) {
                onChange(null);
              }
            }}
          />
          {(value || acceptedNumberOnly) && (
            <Check
              className="pointer-events-none absolute right-2 top-2.5 h-4 w-4 text-emerald-600"
              aria-label={
                value
                  ? `Selected pallet ${value.pallet_number}`
                  : `Accepted mapped pallet ${query}`
              }
            />
          )}
        </div>
      </label>
      {open && !unavailable && (
        <div
          id={listId}
          role="listbox"
          className="absolute z-40 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-slate-200 bg-white p-1 shadow-lg"
        >
          {!required && (
            <button
              type="button"
              role="option"
              aria-selected={!value && !query.trim()}
              className="w-full rounded px-3 py-2 text-left text-sm font-medium text-brand-700 hover:bg-brand-50"
              onMouseDown={(event) => event.preventDefault()}
              onClick={leaveUnassigned}
            >
              Leave Unassigned
            </button>
          )}
          {options.isLoading && (
            <p className="px-3 py-2 text-sm text-slate-500">Searching…</p>
          )}
          {items.map((option) => (
            <button
              key={option.id}
              type="button"
              role="option"
              aria-selected={option.id === value?.id}
              disabled={!option.is_active}
              className="flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm hover:bg-brand-50"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => select(option)}
            >
              <span>{option.pallet_number}{!option.is_active ? " (archived)" : ""}</span>
              <span className="text-right text-xs text-slate-500">
                {palletWarehouseDistributionLabel(option.warehouse_names)}
              </span>
            </button>
          ))}
          {!options.isLoading && items.length === 0 && !canOfferCreate && (
            <p className="px-3 py-2 text-sm text-slate-500">
              No pallets in this lot.
            </p>
          )}
          {canOfferCreate && (
            <button
              type="button"
              className="flex w-full items-center gap-2 border-t border-slate-100 px-3 py-2 text-left text-sm text-brand-700"
              disabled={create.isPending}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => void createAndSelect()}
            >
              <Plus className="h-4 w-4" />
              {create.isPending
                ? "Creating…"
                : `Create “${query.trim().replace(/\s+/g, " ")}”`}
            </button>
          )}
        </div>
      )}
      <p className="mt-1 text-xs text-slate-500">
        {acceptedNumberOnly && !value
          ? "Accepted from the spreadsheet; inventory review will resolve it. "
          : !required
            ? "Leave blank to receive this box as Unassigned. "
            : ""}
        Pallets span warehouses through their boxes. The same pallet can be
        selected for receipts at different warehouses.
      </p>
      {error && (
        <p role="alert" className="mt-1 text-xs text-rose-600">
          {error}
        </p>
      )}
    </div>
  );
}
