import type { Performer } from "@/api/types";

export function PerformersTable({
  title,
  performers,
  emptyHint,
}: {
  title: string;
  performers: Performer[];
  emptyHint: string;
}) {
  if (performers.length === 0) {
    if (!emptyHint) return null;
    return (
      <div className="border-t border-slate-100 px-5 py-3 text-xs text-slate-400">
        {title} — {emptyHint}
      </div>
    );
  }
  return (
    <div className="border-t border-slate-100 px-5 py-3">
      <div className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">
        {title}
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-slate-100">
          {performers.map((p) => (
            <tr key={p.employee_id}>
              <td className="py-1.5 pr-2">{p.employee_name}</td>
              <td className="py-1.5 pr-2 text-right text-xs text-slate-500">
                {p.pages} pages · {p.hours.toFixed(2)}h
              </td>
              <td className="py-1.5 text-right font-semibold tabular-nums">
                {p.pages_per_day.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
