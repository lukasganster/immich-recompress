import { ChangeDetectionStrategy, Component, computed, inject, output } from '@angular/core';
import { StoreService } from '../../services/store.service';

/** Photo format toggles for the filter bar, built from the last scan's counts. */
@Component({
  selector: 'app-format-filter',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './format-filter.html',
})
export class FormatFilterComponent {
  readonly store = inject(StoreService);
  /** The filter changed and the list needs loading again. */
  readonly changed = output<void>();

  /** Scanned formats with counts, keeping chosen ones that a rescan no longer found. */
  readonly options = computed(() => {
    const counts = this.store.formats();
    const names = [...Object.keys(counts), ...this.store.formatFilter().filter(f => !(f in counts))];
    return names.map(name => ({ name, count: counts[name] ?? 0 }));
  });

  toggle(name: string): void {
    this.store.formatFilter.update(list =>
      list.includes(name) ? list.filter(f => f !== name) : [...list, name]);
    // A selection hidden by the new filter would still be sent by the bulk bar.
    this.store.clearSelection();
    this.store.page.set(1);
    this.changed.emit();
  }
}
