import type { BarcodeResolution } from "@/api/types";

const CANONICAL_BARCODE = /^(LOT|PAL|BOX|FIL)-(\d{12})-(\d)$/;
const COMPACT_BARCODE = /^(LOT|PAL|BOX|FIL)(\d{12})(\d)$/;

export type BarcodeValidation =
  | { ok: true; value: string }
  | { ok: false; reason: "format" | "checksum"; message: string };

export function barcodeCheckDigit(body: string): number {
  if (!/^\d{12}$/.test(body)) {
    throw new Error("Barcode body must contain exactly 12 digits.");
  }
  const weighted = [...body]
    .reverse()
    .reduce((total, digit, index) => total + Number(digit) * (index % 2 === 0 ? 3 : 1), 0);
  return (10 - (weighted % 10)) % 10;
}

export function canonicalizeBarcode(raw: string): BarcodeValidation {
  const cleaned = raw
    .replace(/[\u0000-\u001f\u007f]/g, "")
    .trim()
    .toUpperCase()
    .replace(/[\s_]+/g, "-")
    .replace(/-+/g, "-");
  const compact = cleaned.match(COMPACT_BARCODE);
  const value = compact
    ? `${compact[1]}-${compact[2]}-${compact[3]}`
    : cleaned;
  const match = value.match(CANONICAL_BARCODE);
  if (!match || Number(match[2]) === 0) {
    return {
      ok: false,
      reason: "format",
      message: "Use a generated barcode such as BOX-000000000001-7.",
    };
  }
  if (barcodeCheckDigit(match[2]) !== Number(match[3])) {
    return {
      ok: false,
      reason: "checksum",
      message: "The barcode check digit is invalid. Scan the label again.",
    };
  }
  return { ok: true, value };
}

export function resolutionDestination(resolution: BarcodeResolution): {
  path: string | null;
  message: string;
  tone: "success" | "warning" | "danger";
} {
  if (resolution.retired || resolution.lifecycle_state === "retired") {
    return {
      path: null,
      message: `This ${resolution.entity_kind} barcode is a retired tombstone and no longer opens a record.`,
      tone: "danger",
    };
  }
  if (resolution.redirect) {
    return {
      path: resolution.redirect.frontend_path,
      message:
        resolution.lifecycle_state === "merged"
          ? `This lot was merged into ${resolution.redirect.display_label}.`
          : `This pallet was absorbed into ${resolution.redirect.display_label}.`,
      tone: "warning",
    };
  }
  if (resolution.lifecycle_state === "archived") {
    return {
      path: resolution.frontend_path,
      message: `${resolution.display_label} is archived.`,
      tone: "warning",
    };
  }
  if (resolution.lifecycle_state === "returned") {
    return {
      path: resolution.frontend_path,
      message: `${resolution.display_label} has been returned.`,
      tone: "warning",
    };
  }
  return {
    path: resolution.frontend_path,
    message: `${resolution.display_label} found.`,
    tone: "success",
  };
}

export function stripGeneratedBarcodeFields<T>(value: T): T {
  if (Array.isArray(value)) {
    return value.map(stripGeneratedBarcodeFields) as T;
  }
  if (!value || typeof value !== "object") return value;
  if (
    value instanceof Date ||
    (typeof FormData !== "undefined" && value instanceof FormData) ||
    (typeof File !== "undefined" && value instanceof File)
  ) {
    return value;
  }
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([key]) => key !== "barcode")
      .map(([key, item]) => [key, stripGeneratedBarcodeFields(item)]),
  ) as T;
}
