import { ChangeDetectionStrategy, Component, inject, output, signal } from '@angular/core';
import { ApiService } from '../../services/api.service';
import { StoreService } from '../../services/store.service';
import { JobPublic } from '../../models/api.models';

@Component({
  selector: 'app-review-modal',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './review-modal.html',
  styleUrl: './review-modal.css',
})
export class ReviewModalComponent {
  private readonly api = inject(ApiService);
  readonly store = inject(StoreService);

  readonly closed = output<void>();
  readonly open = signal(false);
  readonly currentId = signal<string | null>(null);
  readonly currentJob = signal<JobPublic | null>(null);

  show(id: string): void {
    const job = this.store.jobs()[id];
    if (!job) return;
    this.currentId.set(id);
    this.currentJob.set(job);
    this.open.set(true);
  }

  hide(): void {
    this.open.set(false);
    this.currentId.set(null);
    this.currentJob.set(null);
    this.closed.emit();
  }

  confirm(): void {
    const id = this.currentId();
    if (id) this.api.confirm(id).subscribe(() => this.hide());
  }

  discard(): void {
    const id = this.currentId();
    if (id) this.api.discard(id).subscribe(() => this.hide());
  }

  humanMB(bytes: number | null | undefined): string {
    if (!bytes) return '0 MB';
    const mb = bytes / (1024 * 1024);
    if (mb >= 1024) return (mb / 1024).toFixed(2) + ' GB';
    return mb.toFixed(1) + ' MB';
  }

  pct(j: JobPublic): string {
    if (j.old_size == null || j.new_size == null || !j.old_size) return '0';
    return (100 - (j.new_size / j.old_size * 100)).toFixed(1);
  }

  /** A motion-photo job whose motion video is being recompressed (vs. removed). */
  isMotionRecompress(j: JobPublic): boolean {
    return j.media === 'motionphoto' && j.motion_action === 'recompress';
  }

  formatLabel(j: JobPublic): string {
    const from = j.codec ? j.codec.toUpperCase() : '?';
    if (j.media === 'motionphoto') {
      return j.motion_action === 'recompress'
        ? 'Live Photo motion → recompressed (HEVC)'
        : 'Live Photo → still (motion video removed)';
    }
    if (j.media === 'image') return `${from} → JPEG · .jpg`;
    const ENC: Record<string, string> = {
      x265: 'HEVC (x265)', nvenc_h265: 'HEVC (NVENC)',
      qsv_h265: 'HEVC (QSV)', vce_h265: 'HEVC (VCE)',
    };
    return `${from} → ${ENC[j.encoder] ?? j.encoder ?? 'HEVC'} · .mp4`;
  }

  previewUrl(id: string, media: string): string {
    return this.api.previewUrl(id) + (media === 'image' ? '' : '');
  }

  thumbUrl(id: string): string {
    return this.api.thumbnailUrl(id, 'preview');
  }
}
