import { describe, expect, it, vi } from "vitest";
import appShellSource from "@/components/AppShell.tsx?raw";
import barcodeDisplaySource from "@/components/BarcodeDisplay.tsx?raw";
import scannerSource from "@/components/BarcodeScannerDialog.tsx?raw";
import printableSource from "@/components/PrintableLabelDialog.tsx?raw";
import fileItemsSource from "@/components/FileItemsEditor.tsx?raw";
import mapperSource from "@/components/ExcelRowMapper.tsx?raw";
import boxDetailSource from "@/pages/BoxDetailPage.tsx?raw";
import fileDetailSource from "@/pages/FileDetailPage.tsx?raw";
import lotDetailSource from "@/pages/LotDetailPage.tsx?raw";
import palletDetailSource from "@/pages/PalletDetailPage.tsx?raw";
import hooksSource from "@/api/hooks.ts?raw";
import {
  barcodeCheckDigit,
  canonicalizeBarcode,
  resolutionDestination,
  stripGeneratedBarcodeFields,
} from "@/lib/barcode";
import { copyBarcodeValue } from "@/components/BarcodeDisplay";
import { printBarcodeLabel } from "@/components/PrintableLabelDialog";
import type { BarcodeResolution } from "@/api/types";

function resolution(
  patch: Partial<BarcodeResolution> = {},
): BarcodeResolution {
  return {
    entity_kind: "box",
    entity_id: 4,
    barcode: "BOX-000000000001-7",
    lifecycle_state: "active",
    retired: false,
    frontend_path: "/boxes/4",
    display_label: "Box 4",
    hierarchy: {},
    redirect: null,
    retired_at: null,
    retirement_reason: null,
    retirement_operation: null,
    retirement_metadata: null,
    ...patch,
  };
}

describe("generated barcode formatting and rendering", () => {
  it("canonicalizes scanner text and validates the GS1 Mod10 digit", () => {
    expect(barcodeCheckDigit("000000000001")).toBe(7);
    expect(canonicalizeBarcode(" box0000000000017\n")).toEqual({
      ok: true,
      value: "BOX-000000000001-7",
    });
    expect(canonicalizeBarcode("BOX-000000000001-6")).toMatchObject({
      ok: false,
      reason: "checksum",
    });
  });

  it("uses an accessible Code 128 SVG and readable value", () => {
    expect(barcodeDisplaySource).toContain('format: "CODE128"');
    expect(barcodeDisplaySource).toContain('role="img"');
    expect(barcodeDisplaySource).toContain("Generated immutable barcode");
    expect(barcodeDisplaySource).toContain('variant === "compact"');
    expect(barcodeDisplaySource).toContain("Barcode image unavailable");
  });

  it("reports copy and print helper failures to callers", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    await copyBarcodeValue("FIL-000000000001-7", { writeText });
    expect(writeText).toHaveBeenCalledWith("FIL-000000000001-7");
    await expect(
      copyBarcodeValue("FIL-000000000001-7", undefined),
    ).rejects.toThrow(/Clipboard/);

    const print = vi.fn();
    printBarcodeLabel(print);
    expect(print).toHaveBeenCalledOnce();
  });
});

describe("immutable barcode forms and payloads", () => {
  it("does not offer File or workbook barcode inputs/mappings", () => {
    expect(fileItemsSource).not.toContain('name="barcode"');
    expect(fileItemsSource).not.toContain("setBarcode");
    expect(mapperSource).not.toContain('label="Barcode');
    expect(mapperSource).not.toContain("barcodeColumn");
    expect(fileItemsSource).toContain("generated automatically");
    expect(mapperSource).toContain("never mapped or");
    expect(fileDetailSource).toContain("cannot be edited");
    expect(boxDetailSource).toContain("cannot be edited");
  });

  it("strips generated barcode fields recursively before mutations", () => {
    expect(
      stripGeneratedBarcodeFields({
        barcode: "BOX-000000000001-7",
        box_number: "1",
        files: [{ reference: "A", barcode: "FIL-000000000002-4" }],
      }),
    ).toEqual({ box_number: "1", files: [{ reference: "A" }] });
    expect(hooksSource).toContain("stripGeneratedBarcodeFields(input)");
    expect(hooksSource).toContain("stripGeneratedBarcodeFields(input.payload)");
  });
});

describe("label and scanner lifecycle wiring", () => {
  it("wires print labels into every hierarchy detail header", () => {
    for (const source of [
      lotDetailSource,
      palletDetailSource,
      boxDetailSource,
      fileDetailSource,
    ]) {
      expect(source).toContain("<PrintLabelButton");
      expect(source).toContain("<PrintableLabelDialog");
      expect(source).toContain("<BarcodeDisplay");
    }
    expect(printableSource).toContain("window.print");
    expect(printableSource).toContain('event.key === "Escape"');
    expect(printableSource).toContain("returnFocus?.focus()");
  });

  it("resolves active, archived, redirect, and retired destinations", () => {
    expect(resolutionDestination(resolution()).path).toBe("/boxes/4");
    expect(
      resolutionDestination(
        resolution({ lifecycle_state: "archived" }),
      ).message,
    ).toMatch(/archived/);
    expect(
      resolutionDestination(
        resolution({
          entity_kind: "lot",
          lifecycle_state: "merged",
          redirect: {
            entity_kind: "lot",
            entity_id: 9,
            barcode: "LOT-000000000009-3",
            frontend_path: "/lots/9",
            display_label: "Lot Nine",
          },
        }),
      ).path,
    ).toBe("/lots/9");
    expect(
      resolutionDestination(
        resolution({
          lifecycle_state: "retired",
          retired: true,
          frontend_path: null,
        }),
      ).path,
    ).toBeNull();
  });

  it("provides global scanner navigation and lifecycle messaging", () => {
    expect(appShellSource).toContain("<BarcodeScannerDialog");
    expect(appShellSource).toContain("<ScanBarcodeButton");
    expect(scannerSource).toContain("navigate(destination.path)");
    expect(scannerSource).toContain("Barcode not found or inaccessible");
    expect(scannerSource).toContain("Invalid checksum");
    expect(scannerSource).toContain("Camera");
    expect(scannerSource).toContain('event.key === "Escape"');
  });
});
