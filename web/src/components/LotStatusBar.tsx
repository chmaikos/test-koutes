import type { LotStatusCounts } from "@/api/types";
import { STATUS_LABEL } from "@/components/StatusBadge";
import { lotStatusSegments } from "@/pages/lots";

const COLORS = {
  quarantined: "bg-rose-500",
  received: "bg-sky-500",
  processing: "bg-indigo-500",
  incomplete: "bg-amber-500",
  ready_to_return: "bg-violet-500",
  returned: "bg-emerald-500",
} as const;

export function LotStatusBar({
  counts,
  showLegend = true,
}: {
  counts: LotStatusCounts;
  showLegend?: boolean;
}) {
  const segments = lotStatusSegments(counts);
  const total = segments.reduce((sum, segment) => sum + segment.count, 0);
  const label =
    total === 0
      ? "No boxes"
      : segments
          .map(
            (segment) =>
              `${STATUS_LABEL[segment.status]}: ${segment.count}`,
          )
          .join(", ");

  return (
    <div>
      <div
        className="flex h-2.5 w-full overflow-hidden rounded-full bg-slate-100"
        role="img"
        aria-label={`Lot status distribution. ${label}`}
      >
        {segments.map((segment) => (
          <span
            key={segment.status}
            className={COLORS[segment.status]}
            style={{ width: `${segment.percent}%` }}
            title={`${STATUS_LABEL[segment.status]}: ${segment.count}`}
          />
        ))}
      </div>
      {showLegend && segments.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-600">
          {segments.map((segment) => (
            <li key={segment.status} className="inline-flex items-center gap-1">
              <span
                className={`h-2 w-2 rounded-full ${COLORS[segment.status]}`}
                aria-hidden="true"
              />
              {STATUS_LABEL[segment.status]} {segment.count}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

