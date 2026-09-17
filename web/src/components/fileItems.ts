import type { FileInput } from "@/api/types";

export function normalizeFileReference(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

export function cleanFileItems(items: FileInput[]): FileInput[] {
  return items.map((item) => ({
    reference: item.reference.trim().replace(/\s+/g, " "),
    description: item.description?.trim() || undefined,
    barcode: item.barcode?.trim() || undefined,
  }));
}

export function validateFileItems(
  items: FileInput[],
  options: { requireAtLeastOne?: boolean } = {},
): string | null {
  if (options.requireAtLeastOne && items.length === 0) {
    return "Add at least one physical File.";
  }
  const seen = new Set<string>();
  for (const [index, item] of items.entries()) {
    const reference = normalizeFileReference(item.reference);
    if (!reference) return `File ${index + 1} needs a reference.`;
    if (seen.has(reference)) {
      return `File reference “${item.reference.trim()}” is duplicated.`;
    }
    seen.add(reference);
  }
  return null;
}
