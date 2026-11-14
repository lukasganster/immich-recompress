/** Bits per second as a short label ("18.4 Mbps", "850 kbps"). Unknown
 *  rates get the same placeholder glyph the rest of the UI uses. */
export function formatBitrate(bps: unknown): string {
  const n = typeof bps === 'number' ? bps : Number(bps);
  if (bps === null || bps === undefined || !Number.isFinite(n) || n <= 0) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} Mbps`;
  return `${Math.round(n / 1000)} kbps`;
}
