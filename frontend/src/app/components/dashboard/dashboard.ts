import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { StoreService } from '../../services/store.service';

/** One segment of the composition band. */
interface Segment {
  readonly pct: number;
  readonly bytes: number;
}

@Component({
  selector: 'app-dashboard',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.css',
})
export class DashboardComponent {
  readonly store = inject(StoreService);

  /**
   * The band draws the whole library to scale: what recompressing already
   * recovered, and what is still on disk. Its denominator therefore includes
   * the already-recovered bytes, which are no longer part of totalSize. It
   * states no estimate of what could still be recovered, because that number
   * is a guess and this band only reports measured facts.
   */
  private readonly denominator = computed(() =>
    this.store.savedBytes() + this.store.totalSize());

  readonly reclaimed = computed<Segment>(() => this.segment(this.store.savedBytes()));

  readonly onDisk = computed<Segment>(() => this.segment(this.store.totalSize()));

  private segment(bytes: number): Segment {
    const denom = this.denominator();
    return { bytes, pct: denom > 0 ? (bytes / denom) * 100 : 0 };
  }

  readonly selectionCount = computed(() => this.store.selected().size);

  humanMB(bytes: number): string {
    if (!bytes) return '0 MB';
    const mb = bytes / (1024 * 1024);
    if (mb >= 1024) return (mb / 1024).toFixed(2) + ' GB';
    return mb.toFixed(1) + ' MB';
  }

  humanTime(sec: number): string {
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return `~${sec} s`;
    if (sec < 3600) return `~${Math.round(sec / 60)} min`;
    const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
    return `~${h} h${m ? ' ' + m + ' min' : ''}`;
  }

  get noun(): string {
    const m = this.store.media();
    return m === 'image' ? 'photos' : m === 'motionphoto' ? 'Live Photos' : 'videos';
  }

  /** The selection's own estimate, shown beside the library's. */
  get selectionEstimate(): string {
    const bytes = this.store.estimatedSavingsBytes();
    const frac = Math.round(this.store.estimatedSavingsFraction() * 100);
    return `${this.humanMB(bytes)} from ${this.selectionCount()} selected (−${frac}%)`;
  }

  get selectionTime(): string {
    return this.humanTime(this.store.estimatedSeconds());
  }
}
