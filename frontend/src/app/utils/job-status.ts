import { JobStatus } from '../models/api.models';

const LABELS: Partial<Record<JobStatus, string>> = { idle: '–', processed: 'done' };

/** Badge text for a job state. A skip names its reason, e.g. "skipped · HEVC". */
export function statusLabel(status: JobStatus, skipReason?: string | null): string {
  if (status === 'skipped' && skipReason) return `skipped · ${skipReason}`;
  return LABELS[status] ?? status;
}
