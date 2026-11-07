/** One way to write a date across the app: the browser's locale, day,
 *  month and year as digits, as Immich does. A date-only value such as a
 *  filter bound (2026-10-07) names a calendar day, not an instant, so it is
 *  read and written in UTC and never shifts a day by timezone. */
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

export function formatDate(value: unknown, timeZone?: string): string {
  if (!value) return '—';
  const text = String(value);
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text.slice(0, 10);
  const options: Intl.DateTimeFormatOptions = {
    year: 'numeric', month: '2-digit', day: '2-digit',
    timeZone: DATE_ONLY.test(text) ? 'UTC' : timeZone,
  };
  try {
    return new Intl.DateTimeFormat(undefined, options).format(parsed);
  } catch {
    // An unknown timezone name; fall back to the browser's own.
    return new Intl.DateTimeFormat(undefined, { ...options, timeZone: undefined }).format(parsed);
  }
}
