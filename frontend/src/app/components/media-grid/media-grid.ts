import {
  ChangeDetectionStrategy, Component, computed, inject,
  OnInit, output, signal, TemplateRef, viewChild,
} from '@angular/core';
import { NgClass } from '@angular/common';
import { UiGridComponent, GridOptions, GridColumnDef, GridCellTemplateContext } from '@ornery/ui-grid';
import { StoreService } from '../../services/store.service';
import { ApiService } from '../../services/api.service';
import { VideoSummary, JobStatus } from '../../models/api.models';

@Component({
  selector: 'app-media-grid',
  standalone: true,
  imports: [UiGridComponent, NgClass],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './media-grid.html',
  styleUrl: './media-grid.css',
})
export class MediaGridComponent implements OnInit {
  readonly store = inject(StoreService);
  readonly api = inject(ApiService);

  readonly detailRequested = output<string>();

  // Template refs for custom cell rendering
  readonly nameTpl = viewChild.required<TemplateRef<GridCellTemplateContext>>('nameTpl');
  readonly sizeTpl = viewChild.required<TemplateRef<GridCellTemplateContext>>('sizeTpl');
  readonly statusTpl = viewChild.required<TemplateRef<GridCellTemplateContext>>('statusTpl');
  readonly actionsTpl = viewChild.required<TemplateRef<GridCellTemplateContext>>('actionsTpl');
  readonly selectTpl = viewChild.required<TemplateRef<GridCellTemplateContext>>('selectTpl');
  readonly selectHeaderTpl = viewChild.required<TemplateRef<unknown>>('selectHeaderTpl');

  private tplsReady = signal(false);

  gridOptions = computed<GridOptions | null>(() => {
    if (!this.tplsReady()) return null;
    const hideClipCols = this.store.media() !== 'video';  // duration/codec only apply to videos
    const cols: GridColumnDef[] = [
      {
        name: 'select', displayName: ' ', field: 'id', width: '44px',
        enableSorting: false, enableFiltering: false,
        cellTemplate: this.selectTpl() as TemplateRef<GridCellTemplateContext>,
      },
      {
        name: 'name', displayName: 'Name', field: 'name', enableSorting: true,
        cellTemplate: this.nameTpl() as TemplateRef<GridCellTemplateContext>,
        width: '260px',
      },
      {
        name: 'size', displayName: 'Size', field: 'size', enableSorting: true,
        cellTemplate: this.sizeTpl() as TemplateRef<GridCellTemplateContext>,
        width: '110px',
      },
      { name: 'resolution', displayName: 'Resolution', field: 'resolution', enableSorting: false, width: '110px' },
      ...(hideClipCols ? [] : [
        { name: 'duration', displayName: 'Duration', field: 'duration_human', enableSorting: true, width: '80px' } as GridColumnDef,
        { name: 'codec', displayName: 'Codec', field: 'codec', enableSorting: false, width: '80px' } as GridColumnDef,
      ]),
      { name: 'date', displayName: 'Date', field: 'date', enableSorting: true, width: '100px',
        formatter: (v) => v ? new Date(String(v)).toLocaleDateString('en-US') : '—' },
      { name: 'owner_name', displayName: 'User', field: 'owner_name', enableSorting: false, width: '110px' },
      {
        name: 'status', displayName: 'Status', field: 'status', enableSorting: false, width: '140px',
        cellTemplate: this.statusTpl() as TemplateRef<GridCellTemplateContext>,
      },
      {
        name: 'actions', displayName: '', field: 'id', enableSorting: false, width: '160px',
        cellTemplate: this.actionsTpl() as TemplateRef<GridCellTemplateContext>,
      },
    ];

    return {
      id: 'media-grid',
      data: this.store.videos() as unknown as readonly Record<string, unknown>[],
      columnDefs: cols,
      enableSorting: true,
      enableFiltering: false,
      enablePagination: true,
      enablePaginationControls: true,
      useExternalPagination: true,
      paginationPageSizes: [10, 25, 50, 100],
      paginationPageSize: this.store.perPage(),
      paginationCurrentPage: this.store.page() - 1,
      totalItems: this.store.total(),
      emptyMessage: this.store.loaded() ? 'No assets found' : 'No assets loaded — select a media type to get started.',
      onRegisterApi: (api) => {
        const gridApi = api as {
          pagination?: {
            on?: { paginationChanged?: (cb: (page: number, size: number) => void) => void };
          };
          core?: {
            on?: { sortChanged?: (cb: (col: string | null, dir: string) => void) => void };
          };
        };
        gridApi.pagination?.on?.paginationChanged?.((page, size) => {
          this.store.page.set(page + 1);
          this.store.perPage.set(size);
          this.store.saveBrowse();
          this.load();
        });
        gridApi.core?.on?.sortChanged?.((col, dir) => {
          if (!col) return;
          const fieldMap: Record<string, string> = { name: 'name', size: 'size', duration: 'duration', date: 'date' };
          const sortField = fieldMap[col] ?? 'size';
          this.store.sort.set(sortField as never);
          this.store.order.set(dir === 'asc' ? 'asc' : 'desc');
          this.store.page.set(1);
          this.load();
        });
      },
    };
  });

  ngOnInit(): void {
    // grid options computed after view is ready
    setTimeout(() => this.tplsReady.set(true), 0);
  }

  /** Fetch the asset list. Pass `showOverlay` for user-initiated heavy loads
   *  (Select Media apply) so a "Loading …" dialog covers the pending request. */
  load(showOverlay = false): void {
    const s = this.store;
    if (showOverlay) s.loading.set(true);
    this.api.assets({
      page: s.page(), per_page: s.perPage(), sort: s.sort(), order: s.order(),
      media: s.media(), min_mb: s.effectiveMinMb(),
      codec: s.codec() || undefined, user: s.userFilter() || undefined,
      search: s.search() || undefined, keys: s.selectedKeys(),
    }).subscribe({
      next: data => {
        s.videos.set(data.assets ?? []);
        s.total.set(data.total ?? 0);
        s.totalSize.set(data.total_size ?? 0);
        s.totalPotential.set(data.total_potential ?? 0);
        s.perPage.set(data.per_page ?? s.perPage());
        s.loaded.set(true);
        s.loadError.set(data.error ?? null);
        s.loading.set(false);
      },
      error: () => {
        s.loadError.set('Failed to load');
        s.loaded.set(true);
        s.loading.set(false);
      },
    });
  }

  asVideo(row: Record<string, unknown>): VideoSummary {
    return row as unknown as VideoSummary;
  }

  effectiveStatus(row: Record<string, unknown>): JobStatus {
    return this.store.effectiveStatus(this.asVideo(row));
  }

  isSelected(id: unknown): boolean {
    return this.store.selected().has(String(id));
  }

  /** A row is locked (not selectable) while busy or once already optimized. */
  isLocked(row: Record<string, unknown>): boolean {
    const st = this.store.effectiveStatus(this.asVideo(row));
    return this.store.isBusy(st) || this.store.isDone(st);
  }

  private isLockedVideo(v: VideoSummary): boolean {
    const st = this.store.effectiveStatus(v);
    return this.store.isBusy(st) || this.store.isDone(st);
  }

  toggleSelect(id: unknown): void {
    const v = this.store.videos().find(x => x.id === String(id));
    if (!v || this.isLockedVideo(v)) return;
    this.store.toggleSelect(String(id));
  }

  isAllSelected(): boolean {
    const sel = this.store.selected();
    const vids = this.store.videos();
    return vids.length > 0 && vids.every(v =>
      sel.has(v.id) || this.isLockedVideo(v)
    );
  }

  toggleAll(): void {
    if (this.isAllSelected()) {
      this.store.clearSelection();
    } else {
      const ids = this.store.videos()
        .filter(v => !this.isLockedVideo(v))
        .map(v => v.id);
      this.store.selectAll(ids);
    }
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

  jobResult(v: VideoSummary): { oldSize: number; newSize: number } | null {
    const j = this.store.jobs()[v.id];
    if (j?.new_size != null) return { oldSize: j.old_size ?? 0, newSize: j.new_size };
    const p = this.store.processed()[v.id];
    if (p?.new_size != null) return { oldSize: p.old_size ?? 0, newSize: p.new_size };
    return null;
  }

  openInImmich(v: VideoSummary): void {
    const base = this.store.immichUrl();
    if (base) window.open(`${base}/photos/${encodeURIComponent(v.id)}`, '_blank', 'noopener');
  }

  get immichUrl(): string { return this.store.immichUrl(); }

  readonly Math = Math;
}
