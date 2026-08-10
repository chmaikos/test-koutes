export function toLocalDateTimeInput(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

export function toUtcDateTime(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}

export function validTransportWindow(start: string, end: string): boolean {
  if (!end) return true;
  if (!start) return false;
  return new Date(end).getTime() > new Date(start).getTime();
}
