import { JobStatus } from '../models/api.models';

/* Status groups. Mirrors backend/statuses.py. */
export const WAITING: ReadonlySet<JobStatus> = new Set(['queued', 'queued_replace']);
export const BUSY: ReadonlySet<JobStatus> = new Set(['preparing', 'downloading', 'encoding', 'replacing']);
/** Replacing is irreversible once started, so it cannot be cancelled. */
export const CANCELLABLE: ReadonlySet<JobStatus> = new Set(['queued', 'preparing', 'downloading', 'encoding']);
export const TERMINAL: ReadonlySet<JobStatus> = new Set(['done', 'downloaded', 'encoded', 'skipped', 'error', 'cancelled', 'discarded']);
export const SUCCEEDED: ReadonlySet<JobStatus> = new Set(['done', 'downloaded', 'encoded']);
/** Already-optimized success states, not eligible for (re-)selection. */
export const DONE: ReadonlySet<JobStatus> = new Set([...SUCCEEDED, 'processed']);

const LABELS: Partial<Record<JobStatus, string>> = { idle: '–', processed: 'done', preparing: 'checking', queued_replace: 'confirmed' };

/** Badge text for a job state. A skip names its reason, e.g. "skipped · HEVC". */
export function statusLabel(status: JobStatus, skipReason?: string | null): string {
  if (status === 'skipped' && skipReason) return `skipped · ${skipReason}`;
  return LABELS[status] ?? status;
}
