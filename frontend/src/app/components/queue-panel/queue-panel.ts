import { ChangeDetectionStrategy, Component, inject, output } from '@angular/core';
import { NgClass } from '@angular/common';
import { StoreService } from '../../services/store.service';
import { ApiService } from '../../services/api.service';
import { JobPublic, JobStatus } from '../../models/api.models';

@Component({
  selector: 'app-queue-panel',
  standalone: true,
  imports: [NgClass],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './queue-panel.html',
  styleUrl: './queue-panel.css',
})
export class QueuePanelComponent {
  readonly store = inject(StoreService);
  private readonly api = inject(ApiService);

  readonly reviewRequested = output<string>();
  readonly jobsRefresh = output<void>();

  get orderedJobs(): [string, JobPublic][] {
    const jobs = this.store.jobs();
    const active = this.store.activeId();
    return Object.entries(jobs).sort(([a], [b]) => {
      if (a === active) return -1;
      if (b === active) return 1;
      return 0;
    });
  }

  cancel(id: string): void {
    this.api.cancel(id).subscribe(() => this.jobsRefresh.emit());
  }

  confirmReplace(id: string): void {
    this.api.confirm(id).subscribe(() => this.jobsRefresh.emit());
  }

  discard(id: string): void {
    this.api.discard(id).subscribe(() => this.jobsRefresh.emit());
  }

  clearDone(): void {
    this.api.clearDone().subscribe(() => this.jobsRefresh.emit());
  }

  isActive(id: string): boolean {
    const st = this.store.jobs()[id]?.status;
    return id === this.store.activeId() || this.store.isBusy(st as JobStatus);
  }

  get immichUrl(): string { return this.store.immichUrl(); }

  /** Open the job's asset in Immich. Replaced jobs link to the new asset
   *  (the original is trashed); everything else links to the job's own id. */
  openInImmich(id: string, j: JobPublic): void {
    const base = this.store.immichUrl();
    if (!base) return;
    const target = j.new_id || id;
    window.open(`${base}/photos/${encodeURIComponent(target)}`, '_blank', 'noopener');
  }

  humanMB(bytes: number | null | undefined): string {
    if (!bytes) return '0 MB';
    const mb = bytes / (1024 * 1024);
    if (mb >= 1024) return (mb / 1024).toFixed(2) + ' GB';
    return mb.toFixed(1) + ' MB';
  }

  statusLabel(status: JobStatus): string {
    const map: Record<string, string> = {
      idle: '–', queued: 'queued', encoding: 'encoding', downloading: 'downloading',
      replacing: 'replacing', done: 'done', downloaded: 'downloaded', encoded: 'encoded',
      review: 'review', skipped: 'skipped', error: 'error', cancelled: 'cancelled',
      discarded: 'discarded', processed: '✓ done',
    };
    return map[status] ?? status;
  }

  formatLabel(j: JobPublic): string {
    const from = j.codec ? j.codec.toUpperCase() : '?';
    if (j.media === 'motionphoto') {
      return j.motion_action === 'recompress' ? 'Live Photo motion → recompressed' : 'Live Photo → still';
    }
    if (j.media === 'image') return `${from} → JPEG · .jpg`;
    const target = j.new_codec?.toUpperCase() ?? this.store.encoderLabel(j.encoder);
    return `${from} → ${target} · .mp4`;
  }
}
