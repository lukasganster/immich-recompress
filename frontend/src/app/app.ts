import {
  ChangeDetectionStrategy, Component, inject, OnInit, viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { StoreService } from './services/store.service';
import { ApiService } from './services/api.service';
import { EventsService } from './services/events.service';

import { HeaderComponent } from './components/header/header';
import { DashboardComponent } from './components/dashboard/dashboard';
import { SettingsPanelComponent } from './components/settings-panel/settings-panel';
import { MediaGridComponent } from './components/media-grid/media-grid';
import { BulkBarComponent } from './components/bulk-bar/bulk-bar';
import { QueuePanelComponent } from './components/queue-panel/queue-panel';
import { DetailDrawerComponent } from './components/detail-drawer/detail-drawer';
import { ReviewModalComponent } from './components/review-modal/review-modal';
import { BrowseDialogComponent } from './components/browse-dialog/browse-dialog';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    FormsModule,
    HeaderComponent, DashboardComponent, SettingsPanelComponent,
    MediaGridComponent, BulkBarComponent, QueuePanelComponent,
    DetailDrawerComponent, ReviewModalComponent, BrowseDialogComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App implements OnInit {
  readonly store = inject(StoreService);
  private readonly api = inject(ApiService);
  private readonly events = inject(EventsService);

  readonly grid = viewChild.required(MediaGridComponent);
  readonly drawer = viewChild.required(DetailDrawerComponent);
  readonly review = viewChild.required(ReviewModalComponent);
  readonly browse = viewChild.required(BrowseDialogComponent);

  readonly codecs = ['', 'h264', 'hevc', 'av1', '__other__'];
  readonly codecLabels: Record<string, string> = {
    '': 'All', h264: 'h264', hevc: 'hevc', av1: 'av1', '__other__': 'Other',
  };
  searchDebounce: ReturnType<typeof setTimeout> | null = null;

  /** Subtitle for the "Fetching assets" dialog: media type + size threshold. */
  get loadingSubtitle(): string {
    const m = this.store.media();
    const noun = m === 'image' ? 'Photos' : m === 'motionphoto' ? 'Live Photos' : 'Videos';
    const threshold = m === 'video'
      ? `≥ ${this.store.videoMinGb()} GB`
      : `≥ ${this.store.photoMinMb()} MB`;
    return `${noun} · ${threshold}`;
  }

  get statusText(): string {
    if (!this.store.loaded()) return 'Ready — click Select Media above to load media.';
    const s = this.store.queueStats();
    const m = this.store.media();
    const noun = m === 'image' ? 'Photos' : m === 'motionphoto' ? 'Live Photos' : 'Videos';
    return `${this.store.total()} ${noun} · ${s.queued} queued · ${s.active} active · ${s.review} review · ${s.done} done · ${s.skipped} skipped · ${s.error} errors`;
  }

  ngOnInit(): void {
    this.loadInitial();
    this.events.connect();
    this.events.jobUpdate$.subscribe(update => {
      this.store.applyJobUpdate(update);
    });
    this.events.queueUpdate$.subscribe(() => {
      this.api.jobs().subscribe(data => {
        this.store.jobs.set(data.jobs ?? {});
        this.store.activeId.set(data.active);
        this.store.savedBytes.set(data.stats?.saved_bytes ?? 0);
      });
    });
  }

  private loadInitial(): void {
    this.api.status().subscribe(s => {
      this.store.immichUrl.set(s.env?.IMMICH_URL ?? '');
      this.store.immichVersion.set(s.immich_version ?? '');
      this.store.handbrake.set(s.handbrake);
      this.store.ffprobe.set(s.ffprobe);
      this.store.ffmpeg.set(s.ffmpeg);
    });
    this.api.capabilities().subscribe(c => this.store.applyCapabilities(c));
    this.api.users().subscribe(data => this.store.users.set(data.users ?? []));
    this.api.keyOwners().subscribe(data => this.store.keyOwners.set(data.owners ?? []));
    this.api.jobs().subscribe(data => {
      this.store.jobs.set(data.jobs ?? {});
      this.store.activeId.set(data.active);
      this.store.savedBytes.set(data.stats?.saved_bytes ?? 0);
      for (const [id, job] of Object.entries(data.jobs ?? {})) {
        if (job.status === 'done') this.store.markProcessed(id, job);
      }
    });
  }

  onSearch(val: string): void {
    this.store.search.set(val.trim());
    if (this.searchDebounce) clearTimeout(this.searchDebounce);
    this.searchDebounce = setTimeout(() => {
      this.store.page.set(1);
      this.grid().load();
    }, 300);
  }

  setCodec(codec: string): void {
    this.store.codec.set(codec);
    this.store.page.set(1);
    this.grid().load();
  }

  setUser(userId: string): void {
    this.store.userFilter.set(userId);
    this.store.page.set(1);
    this.grid().load();
  }

  onBrowseApply(): void {
    this.grid().load(true);
  }

  onEncode(ids: string[]): void {
    const names: Record<string, string> = {};
    const sizes: Record<string, number> = {};
    const medias: Record<string, string> = {};
    for (const v of this.store.videos()) {
      if (ids.includes(v.id)) { names[v.id] = v.name; sizes[v.id] = v.size; medias[v.id] = v.media; }
    }
    this.api.enqueue(ids, names, sizes, { ...this.store.settings(), media: this.store.media() }, medias).subscribe(() => {
      this.api.jobs().subscribe(data => {
        this.store.jobs.set(data.jobs ?? {});
        this.store.activeId.set(data.active);
      });
    });
  }

  onBulkDownload(): void {
    for (const v of this.store.videos()) {
      if (this.store.selected().has(v.id)) {
        window.open(this.api.downloadUrl(v.id, v.name), '_blank');
      }
    }
  }

  onJobsRefresh(): void {
    this.api.jobs().subscribe(data => {
      this.store.jobs.set(data.jobs ?? {});
      this.store.activeId.set(data.active);
      this.store.savedBytes.set(data.stats?.saved_bytes ?? 0);
    });
  }
}
