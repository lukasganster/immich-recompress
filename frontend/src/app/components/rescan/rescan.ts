import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, output, signal } from '@angular/core';
import { mdiRefresh } from '@mdi/js';
import { IconComponent } from '../icon/icon';
import { StoreService } from '../../services/store.service';

/** Rescan button for the filter bar. Filters reuse the last scan, so this says how old it is. */
@Component({
  selector: 'app-rescan',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './rescan.html',
})
export class RescanComponent {
  readonly store = inject(StoreService);
  readonly rescan = output<void>();
  readonly mdiRefresh = mdiRefresh;

  private readonly now = signal(Date.now());

  readonly age = computed(() => {
    const scanned = Date.parse(this.store.scannedAt() ?? '');
    if (Number.isNaN(scanned)) return '';
    const minutes = Math.max(0, Math.floor((this.now() - scanned) / 60_000));
    if (minutes < 1) return 'Scanned just now';
    return minutes < 60 ? `Scanned ${minutes} min ago` : `Scanned ${Math.floor(minutes / 60)} h ago`;
  });

  constructor() {
    const timer = setInterval(() => this.now.set(Date.now()), 30_000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }
}
