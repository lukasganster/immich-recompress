import { ChangeDetectionStrategy, Component, inject, output } from '@angular/core';
import { mdiArchiveArrowDownOutline, mdiClose, mdiDownload } from '@mdi/js';
import { StoreService } from '../../services/store.service';
import { IconComponent } from '../icon/icon';

@Component({
  selector: 'app-bulk-bar',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './bulk-bar.html',
  styleUrl: './bulk-bar.css',
})
export class BulkBarComponent {
  readonly store = inject(StoreService);
  readonly encodeSelected = output<string[]>();
  readonly downloadSelected = output<void>();
  readonly clearSelection = output<void>();

  readonly mdiArchiveArrowDownOutline = mdiArchiveArrowDownOutline;
  readonly mdiClose = mdiClose;
  readonly mdiDownload = mdiDownload;

  get noun(): string {
    const m = this.store.media();
    return m === 'image' ? 'photos' : m === 'motionphoto' ? 'Live Photos' : 'videos';
  }
  get actionLabel(): string {
    const m = this.store.media();
    return m === 'image' ? 'Compress' : m === 'motionphoto' ? 'Strip motion from' : 'Encode';
  }
  get n(): number { return this.store.selected().size; }
  get show(): boolean { return this.n > 0; }
}
