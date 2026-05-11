/** Today's date as YYYY-MM-DD in the user's local time. */
export function todayStr(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** Monday of the ISO week containing `date`, formatted YYYY-MM-DD. */
export function isoWeekStart(date: string): string {
  const d = new Date(`${date}T00:00:00`);
  const dow = (d.getDay() + 6) % 7; // 0 = Mon, 6 = Sun
  d.setDate(d.getDate() - dow);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** Sunday at the end of the ISO week containing `date`, formatted YYYY-MM-DD. */
export function isoWeekEnd(date: string): string {
  const monday = isoWeekStart(date);
  const d = new Date(`${monday}T00:00:00`);
  d.setDate(d.getDate() + 6);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}
