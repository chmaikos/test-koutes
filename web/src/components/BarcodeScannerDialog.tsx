import { useEffect, useRef, useState } from "react";
import { ArrowRight, ScanBarcode, X } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useBarcode } from "@/api/hooks";
import {
  canonicalizeBarcode,
  resolutionDestination,
  type BarcodeValidation,
} from "@/lib/barcode";

export function BarcodeScannerDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [raw, setRaw] = useState("");
  const [submitted, setSubmitted] = useState<string>();
  const [validation, setValidation] = useState<BarcodeValidation>();
  const resolution = useBarcode(submitted);
  const destination = resolution.data
    ? resolutionDestination(resolution.data)
    : null;

  useEffect(() => {
    const returnFocus = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("keydown", keydown);
      returnFocus?.focus();
    };
  }, [onClose]);

  useEffect(() => {
    if (
      resolution.data?.lifecycle_state === "active" &&
      destination?.path
    ) {
      navigate(destination.path);
      onClose();
    }
  }, [destination?.path, navigate, onClose, resolution.data?.lifecycle_state]);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const result = canonicalizeBarcode(raw);
    setValidation(result);
    setSubmitted(result.ok ? result.value : undefined);
  }

  function openResolved() {
    if (!destination?.path) return;
    navigate(destination.path);
    onClose();
  }

  const notFound =
    resolution.isError &&
    (resolution.error as { response?: { status?: number } }).response?.status ===
      404;

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section
        className="modal-sheet max-w-lg"
        role="dialog"
        aria-modal="true"
        aria-labelledby="scan-barcode-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="scan-barcode-title" className="text-lg font-semibold">
              Scan barcode
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Scan a generated Lot, Pallet, Box, or File barcode. Camera
              scanning is not used.
            </p>
          </div>
          <button
            type="button"
            className="btn-ghost px-2"
            aria-label="Close barcode scanner"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <form className="mt-4 space-y-3" onSubmit={submit}>
          <label className="block">
            <span className="text-xs font-medium text-slate-600">
              Barcode value
            </span>
            <input
              ref={inputRef}
              autoFocus
              autoComplete="off"
              spellCheck={false}
              className="input mt-1 font-mono uppercase"
              placeholder="Scan barcode, then press Enter"
              value={raw}
              onChange={(event) => {
                setRaw(event.target.value);
                setValidation(undefined);
                setSubmitted(undefined);
              }}
              aria-describedby="scan-barcode-help"
            />
          </label>
          <p id="scan-barcode-help" className="text-xs text-slate-500">
            Hardware keyboard-wedge scanners are supported. The value is
            canonicalized and its check digit is verified before lookup.
          </p>
          <button
            className="btn-primary w-full sm:w-auto"
            disabled={!raw.trim() || resolution.isFetching}
          >
            <ScanBarcode className="h-4 w-4" />
            {resolution.isFetching ? "Resolving…" : "Resolve barcode"}
          </button>
        </form>

        {validation && !validation.ok && (
          <p className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800" role="alert">
            <strong>
              {validation.reason === "checksum"
                ? "Invalid checksum."
                : "Invalid barcode."}
            </strong>{" "}
            {validation.message}
          </p>
        )}
        {resolution.isError && (
          <p className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800" role="alert">
            <strong>{notFound ? "Barcode not found or inaccessible." : "Lookup failed."}</strong>{" "}
            {notFound
              ? "Check the label and your warehouse access, then try again."
              : "The resolver could not be reached. Try again."}
          </p>
        )}
        {resolution.data && destination && (
          <div
            className={`mt-4 rounded-lg border p-3 text-sm ${
              destination.tone === "danger"
                ? "border-rose-200 bg-rose-50 text-rose-900"
                : "border-amber-200 bg-amber-50 text-amber-900"
            }`}
            role="status"
          >
            <strong className="block capitalize">
              {resolution.data.lifecycle_state}
            </strong>
            <span>{destination.message}</span>
            {resolution.data.retirement_reason && (
              <span className="mt-1 block">
                Reason: {resolution.data.retirement_reason}
              </span>
            )}
            {destination.path && (
              <button
                type="button"
                className="btn-secondary mt-3"
                onClick={openResolved}
              >
                Open canonical record <ArrowRight className="h-4 w-4" />
              </button>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

export function ScanBarcodeButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="btn-secondary" onClick={onClick}>
      <ScanBarcode className="h-4 w-4" /> Scan barcode
    </button>
  );
}
