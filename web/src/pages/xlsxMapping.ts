import type { InboundRequestItemInput } from "@/api/types";

function canonicalBoxNumber(value: string): string {
  const trimmed = value.trim();
  return /^\d+$/.test(trimmed) ? trimmed.padStart(3, "0") : trimmed;
}

export function groupInboundItems(
  rows: InboundRequestItemInput[],
): InboundRequestItemInput[] {
  const grouped = new Map<
    string,
    { lot: string; box_number: string; contents: string[] }
  >();

  for (const row of rows) {
    const lot = row.lot.trim();
    const boxNumber = canonicalBoxNumber(row.box_number);
    const key = `${lot}\u0000${boxNumber}`;
    let group = grouped.get(key);
    if (!group) {
      group = { lot, box_number: boxNumber, contents: [] };
      grouped.set(key, group);
    }
    const contents = row.contents?.trim();
    if (contents && !group.contents.includes(contents)) {
      group.contents.push(contents);
    }
  }

  return Array.from(grouped.values(), (group) => ({
    lot: group.lot,
    box_number: group.box_number,
    contents: group.contents.join(" | ") || undefined,
  }));
}

export function deliveryVariance(ordered: number, actual: number): number {
  return actual - ordered;
}

export function hasRequiredDiscrepancyReason(
  ordered: number,
  actual: number,
  reason: string,
): boolean {
  return deliveryVariance(ordered, actual) === 0 || reason.trim().length > 0;
}
