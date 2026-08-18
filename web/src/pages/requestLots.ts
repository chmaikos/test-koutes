import type { BoxRequest } from "@/api/types";

export interface RequestLotSummary {
  key: string;
  id: number | null;
  name: string;
}

export function requestLots(request: Pick<BoxRequest, "items">): RequestLotSummary[] {
  const lots = new Map<string, RequestLotSummary>();

  for (const item of request.items) {
    const name = item.lot?.trim();
    if (!name && item.lot_id === null) continue;

    const key = item.lot_id === null ? `name:${name}` : `id:${item.lot_id}`;
    if (!lots.has(key)) {
      lots.set(key, {
        key,
        id: item.lot_id,
        name: name || `Lot #${item.lot_id}`,
      });
    }
  }

  return [...lots.values()].sort((left, right) =>
    left.name.localeCompare(right.name, undefined, { numeric: true }),
  );
}
