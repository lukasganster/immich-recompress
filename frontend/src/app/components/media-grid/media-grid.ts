import {
  ChangeDetectionStrategy, Component, computed, inject,
  ElementRef, OnDestroy, OnInit, output, signal, TemplateRef, viewChild,
} from '@angular/core';
import { NgClass } from '@angular/common';
import { UiGridComponent, GridOptions, GridColumnDef, GridCellTemplateContext } from '@ornery/ui-grid';
import { Subscription } from 'rxjs';
import { MAX_PER_PAGE, StoreService } from '../../services/store.service';
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
export class MediaGridComponent implements OnInit, OnDestroy {
  readonly store = inject(StoreService);
  readonly api = inject(ApiService);
  private readonly host: ElementRef<HTMLElement> = inject(ElementRef);

  readonly detailRequested = output<string>();

  // Template refs for custom cell rendering
  readonly nameTpl = viewChild.required<TemplateRef<GridCellTemplateContext>>('nameTpl');
  readonly sizeTpl = viewChild.required<TemplateRef<GridCellTemplateContext>>('sizeTpl');
  readonly statusTpl = viewChild.required<TemplateRef<GridCellTemplateContext>>('statusTpl');
  readonly actionsTpl = viewChild.required<TemplateRef<GridCellTemplateContext>>('actionsTpl');
  readonly selectTpl = viewChild.required<TemplateRef<GridCellTemplateContext>>('selectTpl');

  private tplsReady = signal(false);
  /** True while any asset request is pending, including a page/filter change. */
  readonly pageLoading = signal(false);
  private assetRequest: Subscription | null = null;
  private sortUnsubscribe: (() => void) | null = null;

  /** Bumped on every successful load. @ornery/ui-grid's Angular cell-template
   *  bridge keys slots by row position, not row id, so swapping in a whole new
   *  page of rows (same slot positions, different ids) leaves stale content in
   *  templated cells (name/size/status/actions). Keying the grid on this value
   *  forces Angular to destroy and recreate it whenever the row set changes,
   *  which sidesteps the bug. Plain non-templated columns aren't affected. */
  readonly loadGen = signal(0);

  readonly totalPages = computed(() =>
    Math.max(1, Math.ceil(this.store.total() / this.store.perPage()))
  );

  readonly firstItem = computed(() =>
    this.store.total() ? (this.store.page() - 1) * this.store.perPage() + 1 : 0
  );

  readonly lastItem = computed(() =>
    Math.min(this.store.page() * this.store.perPage(), this.store.total())
  );

  /** Width of the select column, shared with the "Select all" overlay
   *  checkbox in the template (see media-grid.html) — the grid library only
   *  supports Angular templates for body cells, not header cells, so that
   *  checkbox is a real DOM element positioned on top of this column's
   *  (otherwise blank) header cell rather than rendered by the grid itself. */
  readonly selectColWidth = '4%';
  /** Pinned so the overlay checkbox's CSS height always matches the
   *  rendered header row, regardless of the library's own default.
   *  Must stay in step with --ui-grid-header-cell-padding-block. */
  readonly headerRowHeight = 32;

  gridOptions = computed<GridOptions | null>(() => {
    if (!this.tplsReady()) return null;
    // Before the first load there is nothing to put in a table, and the
    // library's built-in empty state talks about filters and sort order,
    // which is the wrong advice here. Same when the load failed: a table
    // cannot say what went wrong. Render our own state instead.
    if (!this.store.loaded() || this.store.loadError() || this.pageLoading()) return null;
    const hideClipCols = this.store.media() !== 'video';  // duration/codec only apply to videos
    // Size is wide because it carries the drawn quantity as well as the
    // number — the rule beside each value is what makes the expensive rows
    // visible before anything is read. See DESIGN.md.
    const widths = hideClipCols
      ? { select: this.selectColWidth, name: '27%', size: '17%', resolution: '11%', date: '11%', user: '10%', status: '13%', actions: '7%' }
      : { select: this.selectColWidth, name: '20%', size: '16%', resolution: '11%', duration: '9%', codec: '8%', date: '9%', user: '7%', status: '12%', actions: '4%' };
    const cols: GridColumnDef[] = [
      {
        name: 'select', displayName: ' ', field: 'id', width: widths.select,
        enableSorting: false, enableFiltering: false,
        cellTemplate: this.selectTpl() as TemplateRef<GridCellTemplateContext>,
      },
      {
        name: 'name', displayName: 'Name', field: 'name', enableSorting: true,
        cellTemplate: this.nameTpl() as TemplateRef<GridCellTemplateContext>,
        width: widths.name,
      },
      {
        name: 'size', displayName: 'Size', field: 'size', enableSorting: true,
        cellTemplate: this.sizeTpl() as TemplateRef<GridCellTemplateContext>,
        width: widths.size,
      },
      { name: 'resolution', displayName: 'Resolution', field: 'resolution', enableSorting: false, width: widths.resolution },
      ...(hideClipCols ? [] : [
        { name: 'duration', displayName: 'Length', field: 'duration_human', enableSorting: true, width: widths.duration } as GridColumnDef,
        { name: 'codec', displayName: 'Codec', field: 'codec', enableSorting: false, width: widths.codec } as GridColumnDef,
      ]),
      { name: 'date', displayName: 'Date', field: 'date', enableSorting: true, width: widths.date,
        formatter: (v) => v ? String(v).slice(0, 10) : '—' },
      { name: 'owner_name', displayName: 'User', field: 'owner_name', enableSorting: false, width: widths.user },
      {
        name: 'status', displayName: 'State', field: 'status', enableSorting: false, width: widths.status,
        cellTemplate: this.statusTpl() as TemplateRef<GridCellTemplateContext>,
      },
      {
        name: 'actions', displayName: '', field: 'id', enableSorting: false, width: widths.actions,
        cellTemplate: this.actionsTpl() as TemplateRef<GridCellTemplateContext>,
      },
    ];

    return {
      id: 'media-grid',
      data: this.store.videos() as unknown as readonly Record<string, unknown>[],
      columnDefs: cols,
      enableSorting: true,
      enableFiltering: false,
      headerRowHeight: this.headerRowHeight,
      // The backend already returns one page. Keep the grid's internal pager
      // disabled and use the component pager below to request that page.
      enablePagination: false,
      enablePaginationControls: false,
      // Only reachable once a load has happened, so this always talks about
      // filters rather than about getting started.
      emptyMessage: 'Nothing matches the current filters. Widen the search, or lower the size threshold in Select media.',
      onRegisterApi: (api) => {
        // The grid has just built (or rebuilt) its shadow root, so this is
        // the reliable moment to put our rules into it.
        setTimeout(() => this.themeGridShadow(), 0);
        const gridApi = api as {
          core?: {
            on?: { sortChanged?: (cb: (col: string | null, dir: string) => void) => (() => void) };
          };
        };
        // ui-grid calls onRegisterApi again whenever reactive options change.
        // Replace the old sort listener so loads do not multiply over time.
        this.sortUnsubscribe?.();
        this.sortUnsubscribe = gridApi.core?.on?.sortChanged?.((col, dir) => {
          if (!col) return;
          const fieldMap: Record<string, string> = { name: 'name', size: 'size', duration: 'duration', date: 'date' };
          const sortField = fieldMap[col] ?? 'size';
          this.store.sort.set(sortField as never);
          this.store.order.set(dir === 'asc' ? 'asc' : 'desc');
          this.store.page.set(1);
          this.load();
        }) ?? null;
      },
    };
  });

  ngOnInit(): void {
    // grid options computed after view is ready
    setTimeout(() => this.tplsReady.set(true), 0);
  }

  ngOnDestroy(): void {
    this.assetRequest?.unsubscribe();
    this.sortUnsubscribe?.();
  }

  /**
   * The grid renders its rows inside a shadow root, so the sheet's density
   * and truncation rules cannot reach them through the global stylesheet.
   * Everything themeable by custom property is set in styles.css; these are
   * the few rules that need real selectors, adopted into the shadow root.
   * Cheap and idempotent: the sheet is built once and adopted once.
   */
  private themeGridShadow(): void {
    const el = this.host.nativeElement.querySelector('ui-grid-element');
    const root = el?.shadowRoot;
    if (!root || root.adoptedStyleSheets.includes(MediaGridComponent.shadowSheet)) return;
    root.adoptedStyleSheets = [...root.adoptedStyleSheets, MediaGridComponent.shadowSheet];
  }

  private static readonly shadowSheet = (() => {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(`
      /* One line per row. A wrapping cell would break the ledger's
         rhythm and hide the drawn quantity below the fold. */
      .body-cell {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        height: var(--row, 34px);
        align-content: center;
      }
      /* Cell templates are slotted in from the light DOM, and the wrapper
         they land in is a shrink-wrapping flex item. Without this the
         drawn quantity has no width to be drawn across. */
      .cell-shell { min-width: 0; }
      .cell-content { flex: 1 1 auto; min-width: 0; }
      ::slotted(span) { display: block; width: 100%; min-width: 0; }
      .header-label { text-overflow: ellipsis; }
      .header-cell { border-bottom-color: var(--rule-hard); }
      /* Column heads join the app's one label system. */
      .header-label {
        font-size: 10px;
        letter-spacing: 0.09em;
        text-transform: uppercase;
        color: var(--ink-faint);
      }
      .empty-state { color: var(--ink-dim); max-width: none; }
    `);
    return sheet;
  })();

  goToPage(page: number): void {
    const nextPage = Math.min(Math.max(1, page), this.totalPages());
    if (nextPage === this.store.page()) return;
    this.store.page.set(nextPage);
    this.load();
  }

  /** The one option that is not a fixed step says what it actually does. */
  pageSizeLabel(size: number): string {
    return size === this.store.total() && size > 100
      ? `All ${size}`
      : size === MAX_PER_PAGE && this.store.total() > MAX_PER_PAGE
        ? `${size.toLocaleString('en-US')} per page`
        : `${size}`;
  }

  changePageSize(value: unknown): void {
    const size = Number(value);
    if (!this.store.perPageOptions().includes(size)) return;
    this.store.perPage.set(size);
    this.store.page.set(1);
    this.store.saveBrowse();
    this.load();
  }

  /** Fetch the asset list. Pass `showOverlay` for user-initiated heavy loads
   *  (Select Media apply) so a "Loading …" dialog covers the pending request. */
  load(showOverlay = false): void {
    const s = this.store;
    if (showOverlay) s.loading.set(true);
    s.loadError.set(null);
    this.pageLoading.set(true);
    // Abort a superseded page/filter request so a late response cannot replace
    // the data for the page the user most recently selected.
    this.assetRequest?.unsubscribe();
    this.assetRequest = this.api.assets({
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
        this.pageLoading.set(false);
        this.loadGen.update(g => g + 1);
      },
      error: () => {
        // Name the problem and the recovery, not the failure.
        s.loadError.set(
          'Could not reach the backend to list assets. Check that Immich is up and the API key is still valid, then try again.');
        s.videos.set([]);
        s.total.set(0);
        s.totalSize.set(0);
        s.totalPotential.set(0);
        s.loaded.set(true);
        s.loading.set(false);
        this.pageLoading.set(false);
        this.loadGen.update(g => g + 1);
      },
    });
  }

  asVideo(row: Record<string, unknown>): VideoSummary {
    return row as unknown as VideoSummary;
  }

  /**
   * Largest size on the current page. Every row's rule is drawn against
   * this, so the column re-scales per page rather than against the library
   * — the comparison that matters is between the rows you can actually see.
   */
  readonly maxSize = computed(() =>
    this.store.videos().reduce((max, v) => Math.max(max, v.size || 0), 0));

  /** Width of a row's drawn quantity, as a percentage of the widest row. */
  sizePct(row: Record<string, unknown>): number {
    const max = this.maxSize();
    if (!max) return 0;
    const size = Number(row['size']) || 0;
    // Floor at 1% so a genuinely tiny file still draws a visible mark
    // rather than reading as missing data.
    return Math.max(1, (size / max) * 100);
  }

  /** Which meaning-bound ink the rule wears. See DESIGN.md. */
  sizeInk(row: Record<string, unknown>): string {
    const st = this.effectiveStatus(row);
    if (this.store.isDone(st)) return 'done';
    if (this.store.isBusy(st) || this.isSelected(row['id'])) return 'hot';
    return '';
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
      discarded: 'discarded', processed: 'done',
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
