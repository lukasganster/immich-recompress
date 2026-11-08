const LABELS: Record<string, string> = { hevc: 'HEVC', h264: 'H.264', av1: 'AV1', vp9: 'VP9' };

/** Display name for an ffprobe codec name, matching the backend's skip reasons. */
export function codecLabel(codec: string): string {
  return LABELS[codec.toLowerCase()] ?? codec.toUpperCase();
}
