import { useEffect, useRef } from "react";
import { Printer, X } from "lucide-react";
import { BarcodeDisplay } from "@/components/BarcodeDisplay";

export interface PrintableLabel {
  entityType: "Lot" | "Pallet" | "Box" | "File";
  title: string;
  barcode: string;
  context: string[];
}

export function printBarcodeLabel(print: () => void = window.print): void {
  print();
}

export function PrintableLabelDialog({
  label,
  onClose,
}: {
  label: PrintableLabel;
  onClose: () => void;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const returnFocus = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("keydown", keydown);
      returnFocus?.focus();
    };
  }, [onClose]);

  return (
    <div className="modal-backdrop print-label-backdrop" onMouseDown={onClose}>
      <section
        className="modal-sheet max-w-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="print-label-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="no-print mb-4 flex items-center justify-between gap-3">
          <h2 id="print-label-title" className="text-lg font-semibold">
            Print {label.entityType.toLowerCase()} label
          </h2>
          <button
            ref={closeButton}
            type="button"
            className="btn-ghost px-2"
            aria-label="Close print label dialog"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="print-label rounded-lg border-2 border-slate-900 bg-white p-6 text-slate-950">
          <p className="text-xs font-bold uppercase tracking-[0.2em]">
            {label.entityType}
          </p>
          <h3 className="mt-1 break-words text-2xl font-bold">{label.title}</h3>
          {label.context.length > 0 && (
            <ul className="mt-3 space-y-0.5 text-sm">
              {label.context.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
          <BarcodeDisplay value={label.barcode} className="mt-5" />
        </div>
        <div className="no-print mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={() => printBarcodeLabel()}
          >
            <Printer className="h-4 w-4" /> Print label
          </button>
        </div>
      </section>
    </div>
  );
}

export function PrintLabelButton({
  onClick,
}: {
  onClick: () => void;
}) {
  return (
    <button type="button" className="btn-secondary" onClick={onClick}>
      <Printer className="h-4 w-4" /> Print label
    </button>
  );
}
