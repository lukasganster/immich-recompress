import { ChangeDetectionStrategy, Component, inject, output, signal } from '@angular/core';
import { ApiService } from '../../services/api.service';
import { StoreService } from '../../services/store.service';
import { VideoDetail } from '../../models/api.models';

@Component({
  selector: 'app-detail-drawer',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './detail-drawer.html',
  styleUrl: './detail-drawer.css',
})
export class DetailDrawerComponent {
  private readonly api = inject(ApiService);
  readonly store = inject(StoreService);

  readonly open = signal(false);
  readonly detail = signal<VideoDetail | null>(null);
  readonly loading = signal(false);
  readonly currentId = signal<string | null>(null);

  readonly encodeRequested = output<string[]>();
  show(id: string): void {
    this.currentId.set(id);
    this.detail.set(null);
    this.loading.set(true);
    this.open.set(true);
    this.api.assetDetail(id).subscribe({
      next: d => { this.detail.set(d); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  hide(): void { this.open.set(false); }

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

  fmtDate(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d.getTime()) ? '—' : d.toLocaleDateString('en-US');
  }
}
