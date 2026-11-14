import { ChangeDetectionStrategy, Component, HostListener, inject, output, signal } from '@angular/core';
import { CdkTrapFocus } from '@angular/cdk/a11y';
import {
  mdiAccountOutline,
  mdiCalendar,
  mdiCameraIris,
  mdiClose,
  mdiDownload,
  mdiFileVideoOutline,
  mdiFolderOutline,
  mdiHarddisk,
  mdiImageOutline,
  mdiMapMarkerOutline,
  mdiVideoOutline,
} from '@mdi/js';
import { IconComponent } from '../icon/icon';
import { ApiService } from '../../services/api.service';
import { StoreService } from '../../services/store.service';
import { VideoDetail } from '../../models/api.models';
import { formatBitrate } from '../../utils/format-bitrate';
import { formatDate } from '../../utils/format-date';

@Component({
  selector: 'app-detail-drawer',
  standalone: true,
  imports: [CdkTrapFocus, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './detail-drawer.html',
  styleUrl: './detail-drawer.css',
})
export class DetailDrawerComponent {
  readonly mdiAccountOutline = mdiAccountOutline;
  readonly mdiCalendar = mdiCalendar;
  readonly mdiCameraIris = mdiCameraIris;
  readonly mdiClose = mdiClose;
  readonly mdiDownload = mdiDownload;
  readonly mdiFileVideoOutline = mdiFileVideoOutline;
  readonly mdiFolderOutline = mdiFolderOutline;
  readonly mdiHarddisk = mdiHarddisk;
  readonly mdiImageOutline = mdiImageOutline;
  readonly mdiMapMarkerOutline = mdiMapMarkerOutline;
  readonly mdiVideoOutline = mdiVideoOutline;

  readonly api = inject(ApiService);
  readonly store = inject(StoreService);

  readonly open = signal(false);
  readonly detail = signal<VideoDetail | null>(null);
  readonly loading = signal(false);
  readonly currentId = signal<string | null>(null);
  readonly playbackFailed = signal(false);

  readonly encodeRequested = output<string[]>();
  show(id: string): void {
    this.currentId.set(id);
    this.detail.set(null);
    this.playbackFailed.set(false);
    this.loading.set(true);
    this.open.set(true);
    this.api.assetDetail(id).subscribe({
      next: d => { this.detail.set(d); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  hide(): void { this.open.set(false); }

  /** Re-request the detail after a failed load. */
  retry(): void {
    const id = this.currentId();
    if (id) this.show(id);
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.open()) this.hide();
  }

  download(): void {
    const id = this.currentId();
    const d = this.detail();
    if (id) window.open(this.api.downloadUrl(id, d?.name), '_blank');
  }

  encode(): void {
    const id = this.currentId();
    if (id) {
      this.encodeRequested.emit([id]);
      this.hide();
    }
  }

  /** Join the parts that are set, or a dash when none are. */
  joined(sep: string, ...parts: (string | null | undefined)[]): string {
    return parts.filter(p => !!p).join(sep) || '—';
  }

  readonly formatBitrate = formatBitrate;

  fmtDate(iso: string | null | undefined): string {
    return formatDate(iso, this.store.captureTimezone());
  }
}
