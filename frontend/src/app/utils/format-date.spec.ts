import { formatDate } from './format-date';

describe('formatDate', () => {
  const local = (y: number, m: number, d: number) =>
    new Intl.DateTimeFormat(undefined, { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'UTC' })
      .format(Date.UTC(y, m - 1, d));

  it('writes a date-only value as that calendar day in any timezone', () => {
    expect(formatDate('2026-10-07', 'Pacific/Honolulu')).toBe(local(2026, 10, 7));
  });

  it('writes an instant in the given timezone', () => {
    expect(formatDate('2026-10-07T23:30:00Z', 'Europe/Berlin')).toBe(local(2026, 10, 8));
  });

  it('falls back to a dash or the raw date', () => {
    expect(formatDate(null)).toBe('—');
    expect(formatDate('not a date')).toBe('not a date');
  });

  it('survives an unknown timezone name', () => {
    expect(formatDate('2026-10-07T12:00:00Z', 'Not/AZone')).toBe(local(2026, 10, 7));
  });
});
