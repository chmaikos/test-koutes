import { useEffect, useRef, useState } from "react";
import JsBarcode from "jsbarcode";
import { Check, Copy } from "lucide-react";
import clsx from "clsx";

export type BarcodeDisplayVariant = "compact" | "full";

export async function copyBarcodeValue(
  value: string,
  clipboard: Pick<Clipboard, "writeText"> | undefined = navigator.clipboard,
): Promise<void> {
  if (!clipboard?.writeText) {
    throw new Error("Clipboard access is unavailable.");
  }
  await clipboard.writeText(value);
}

export function BarcodeDisplay({
  value,
  variant = "full",
  className,
}: {
  value: string;
  variant?: BarcodeDisplayVariant;
  className?: string;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [renderError, setRenderError] = useState(false);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">(
    "idle",
  );

  useEffect(() => {
    if (!svgRef.current) return;
    try {
      JsBarcode(svgRef.current, value, {
        format: "CODE128",
        displayValue: false,
        height: variant === "compact" ? 28 : 64,
        width: variant === "compact" ? 1.25 : 2,
        margin: variant === "compact" ? 2 : 8,
      });
      setRenderError(false);
    } catch {
      svgRef.current.replaceChildren();
      setRenderError(true);
    }
  }, [value, variant]);

  async function copy() {
    try {
      await copyBarcodeValue(value);
      setCopyStatus("copied");
      window.setTimeout(() => setCopyStatus("idle"), 2_000);
    } catch {
      setCopyStatus("failed");
    }
  }

  return (
    <div
      className={clsx(
        "min-w-0",
        variant === "compact" ? "space-y-0.5" : "space-y-2",
        className,
      )}
      aria-label={`Generated immutable barcode ${value}`}
    >
      {variant === "full" && (
        <div className="overflow-x-auto rounded-md bg-white">
          <svg
            ref={svgRef}
            role="img"
            aria-label={`Code 128 barcode for ${value}`}
            className="mx-auto max-w-full"
          />
        </div>
      )}
      {renderError && (
        <p className="text-xs text-amber-700" role="status">
          Barcode image unavailable; use the readable value.
        </p>
      )}
      <div className="flex min-w-0 items-center gap-1.5">
        <code
          className={clsx(
            "min-w-0 truncate font-mono text-slate-600",
            variant === "compact" ? "text-[11px]" : "text-sm font-medium",
          )}
          title={value}
        >
          {value}
        </code>
        <button
          type="button"
          className="rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-800"
          aria-label={`Copy barcode ${value}`}
          onClick={copy}
        >
          {copyStatus === "copied" ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </button>
        <span className="sr-only" role="status" aria-live="polite">
          {copyStatus === "copied"
            ? "Barcode copied"
            : copyStatus === "failed"
              ? "Barcode could not be copied"
              : ""}
        </span>
      </div>
      {copyStatus === "failed" && (
        <p className="text-xs text-rose-600">Copy failed. Select the value manually.</p>
      )}
    </div>
  );
}
