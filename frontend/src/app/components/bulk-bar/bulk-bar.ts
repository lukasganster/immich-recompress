import { ChangeDetectionStrategy, Component, inject, output } from '@angular/core';
import { StoreService } from '../../services/store.service';

@Component({
  selector: 'app-bulk-bar',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './bulk-bar.html',
  styleUrl: './bulk-bar.css',
})
export class BulkBarComponent {
  readonly store = inject(StoreService);
  readonly encodeSelected = output<string[]>();
  readonly downloadSelected = output<void>();
  readonly clearSelection = output<void>();

  get noun(): string {
    const m = this.store.media();
    return m === 'image' ? 'Photos' : m === 'motionphoto' ? 'Live Photos' : 'Videos';
  }
  get actionLabel(): string {
    const m = this.store.media();
    return m === 'image' ? '▶ Compress All' : m === 'motionphoto' ? 'Strip Motion' : '▶ Encode All';
  }
  get n(): number { return this.store.selected().size; }
  get show(): boolean { return this.n > 0; }
}
