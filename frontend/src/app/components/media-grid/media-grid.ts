import {
  ChangeDetectionStrategy, Component, computed, effect, inject,
  ElementRef, OnDestroy, OnInit, output, signal, TemplateRef, viewChild,
} from '@angular/core';
import { NgClass } from '@angular/common';
import {
  mdiAlertCircleOutline, mdiArrowDown, mdiArrowUp, mdiChevronLeft, mdiChevronRight,
  mdiImageMultipleOutline, mdiInformationOutline, mdiMotionPlayOutline, mdiOpenInNew,
  mdiRefresh, mdiUnfoldMoreHorizontal,
} from '@mdi/js';
import { IconComponent } from '../icon/icon';
import { UiGridComponent, GridOptions, GridColumnDef, GridCellTemplateContext } from '@ornery/ui-grid';
import { Subscription } from 'rxjs';
import { MAX_PER_PAGE, StoreService } from '../../services/store.service';
import { ApiService } from '../../services/api.service';
import { VideoSummary, JobStatus, SortField, SortOrder } from '../../models/api.models';

/** The backend sorts the whole result set and returns one page of it. The
 *  grid would otherwise re-sort that page by its own rules (the Length
 *  column holds text like "1:05", which sorts wrongly as a string). A stable
 *  sort that never reorders keeps the server's order. */
const keepServerOrder = (): number => 0;

/** Sort marks from MDI, the set Immich draws with. The library's own set
 *  uses a stack of bars for "unsorted", which reads as a letter at this
 *  size, so all three are replaced: a faint up/down pair until a column is
 *  sorted, then one arrow pointing the way the values run. */
const SORT_ICONS = {
  sortNone: { path: mdiUnfoldMoreHorizontal },
  sortAsc: { path: mdiArrowUp },
  sortDesc: { path: mdiArrowDown },
};

@Component({
  selector: 'app-media-grid',
  standalone: true,
  imports: [UiGridComponent, NgClass, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './media-grid.html',
  styleUrl: './media-grid.css',
})
export class MediaGridComponent implements OnInit, OnDestroy {
  readonly store = inject(StoreService);
  readonly api = inject(ApiService);
  private readonly host: ElementRef<HTMLElement> = inject(ElementRef);

  readonly detailRequested = output<string>();

  readonly mdiAlertCircleOutline = mdiAlertCircleOutline;
  readonly mdiChevronLeft = mdiChevronLeft;
  readonly mdiChevronRight = mdiChevronRight;
  readonly mdiImageMultipleOutline = mdiImageMultipleOutline;
  readonly mdiInformationOutline = mdiInformationOutline;
  readonly mdiMotionPlayOutline = mdiMotionPlayOutline;
  readonly mdiOpenInNew = mdiOpenInNew;
  readonly mdiRefresh = mdiRefresh;

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
  private resizeObserver: ResizeObserver | null = null;
  private resizeDebounce: ReturnType<typeof setTimeout> | null = null;

  /** Measured width of the ledger. The grid resolves its percentage columns
   *  to fixed pixels when it receives options, and its own resize observer
   *  never rebuilds those tracks, so a window that grows after a load would
   *  leave the table at its old width. Handing it fresh options re-measures. */
  private readonly hostWidth = signal(0);

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
   *  rendered header row, regardless of the library's own default. The
   *  template hands it to the shadow sheet as --app-grid-header-height and
   *  header cells are border-box at that height, so the header paddings in
   *  styles/ui-grid.css cannot drift it. */
  readonly headerRowHeight = 44;
  /** Row height, given to the grid (its scroll maths uses it) and to the
   *  shadow sheet as --app-grid-row-height, so both always agree. Tall
   *  enough for a 36px thumbnail with air, as in Immich's lists. */
  readonly rowHeight = 52;

  /** Grid row key (data-row in the shadow root) of the row under the
   *  pointer. The grid draws rows as loose cells and only hovers the one
   *  cell, so whole-row hover and selection tints are written as rules
   *  keyed on data-row into a per-instance sheet. */
  private readonly hoverRow = signal<string | null>(null);
  private readonly rowSheet = new CSSStyleSheet();

  gridOptions = computed<GridOptions | null>(() => {
    if (!this.tplsReady()) return null;
    // Before the first load there is nothing to put in a table, and the
    // library's built-in empty state talks about filters and sort order,
    // which is the wrong advice here. Same when the load failed: a table
    // cannot say what went wrong. Render our own state instead.
    if (!this.store.loaded() || this.store.loadError() || this.pageLoading()) return null;
    this.hostWidth();
    const hideClipCols = this.store.media() !== 'video';  // duration/codec only apply to videos
    // Size is wide because it carries the drawn quantity as well as the
    // number — the rule beside each value is what makes the expensive rows
    // visible before anything is read. See DESIGN.md.
    const widths = hideClipCols
      ? { select: this.selectColWidth, name: '27%', size: '17%', resolution: '11%', date: '11%', user: '10%', status: '13%', actions: '7%' }
      : { select: this.selectColWidth, name: '19%', size: '14%', resolution: '10%', duration: '7%', codec: '12%', date: '10%', user: '10%', status: '10%', actions: '4%' };
    const cols: GridColumnDef[] = [
      {
        name: 'select', displayName: ' ', field: 'id', width: widths.select,
        enableSorting: false, enableFiltering: false,
        cellTemplate: this.selectTpl() as TemplateRef<GridCellTemplateContext>,
      },
      {
        name: 'name', displayName: 'Name', field: 'name', enableSorting: true, sortingAlgorithm: keepServerOrder,
        cellTemplate: this.nameTpl() as TemplateRef<GridCellTemplateContext>,
        width: widths.name,
      },
      {
        name: 'size', displayName: 'Size', field: 'size', enableSorting: true, sortingAlgorithm: keepServerOrder,
        cellTemplate: this.sizeTpl() as TemplateRef<GridCellTemplateContext>,
        width: widths.size,
      },
      { name: 'resolution', displayName: 'Resolution', field: 'resolution', enableSorting: false, width: widths.resolution },
      ...(hideClipCols ? [] : [
        { name: 'duration', displayName: 'Length', field: 'duration_human', enableSorting: true, sortingAlgorithm: keepServerOrder, width: widths.duration } as GridColumnDef,
        { name: 'codec', displayName: 'Codec', field: 'codec', enableSorting: false, width: widths.codec } as GridColumnDef,
      ]),
      { name: 'date', displayName: 'Date', field: 'date', enableSorting: true, sortingAlgorithm: keepServerOrder, width: widths.date,
        formatter: (v) => this.formatCaptureDate(v) },
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
      id: MediaGridComponent.gridId,
      data: this.store.videos() as unknown as readonly Record<string, unknown>[],
      columnDefs: cols,
      enableSorting: true,
      enableFiltering: false,
      headerRowHeight: this.headerRowHeight,
      rowHeight: this.rowHeight,
      // The backend already returns one page. Keep the grid's internal pager
      // disabled and use the component pager below to request that page.
      enablePagination: false,
      enablePaginationControls: false,
      // Not in the Angular wrapper's types, but passed through to the element.
      ...{ iconOverrides: SORT_ICONS },
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
            sortColumn?: (col: string, dir: SortOrder) => void;
          };
        };
        // ui-grid calls onRegisterApi again whenever reactive options change.
        // Replace the old sort listener so loads do not multiply over time.
        this.sortUnsubscribe?.();
        this.sortUnsubscribe = gridApi.core?.on?.sortChanged?.((col, dir) => {
          const field = MediaGridComponent.sortFields[col ?? ''];
          if (!field) return;
          // The echo of the seed below, not a click.
          if (field === this.store.sort() && dir === this.store.order()) return;
          // The grid is rebuilt after every load, so its own idea of the
          // next direction always starts from scratch. The store owns it.
          this.store.toggleSort(field);
          this.load();
        }) ?? null;
        // Show the server's sort on the fresh grid, so the header icon is
        // right and the next click on that column flips it. Deferred because
        // sorting from inside onRegisterApi would re-enter it.
        const sortCol = this.store.sort();
        const order = this.store.order();
        if (cols.some(c => c.name === sortCol)) {
          setTimeout(() => gridApi.core?.sortColumn?.(sortCol, order), 0);
        }
      },
    };
  });

  constructor() {
    // Selected and hovered rows, as rules on the grid's own row keys. The
    // grid's default key is `${options.id}-${index in data}`, and the data
    // is store.videos() in order, so a row's key follows from its index.
    effect(() => {
      const selected = this.store.selected();
      const hover = this.hoverRow();
      const rule = (key: string, bg: string) =>
        `.body-cell[data-row="${CSS.escape(key)}"] { background: ${bg} !important; }`;
      const rules: string[] = [];
      let hoverIsSelected = false;
      this.store.videos().forEach((v, i) => {
        if (!selected.has(v.id)) return;
        const key = `${MediaGridComponent.gridId}-${i}`;
        if (key === hover) hoverIsSelected = true;
        rules.push(rule(key, key === hover ? 'var(--app-grid-row-selected-hover)' : 'var(--ui-grid-row-selected-bg)'));
      });
      if (hover && !hoverIsSelected) rules.push(rule(hover, 'var(--ui-grid-row-hover)'));
      this.rowSheet.replaceSync(rules.join('\n'));
    });
  }

  ngOnInit(): void {
    // grid options computed after view is ready
    setTimeout(() => this.tplsReady.set(true), 0);
    this.resizeObserver = new ResizeObserver(entries => {
      const width = Math.round(entries[0]?.contentRect.width ?? 0);
      if (this.resizeDebounce) clearTimeout(this.resizeDebounce);
      this.resizeDebounce = setTimeout(() => this.hostWidth.set(width), 100);
    });
    this.resizeObserver.observe(this.host.nativeElement);
  }

  ngOnDestroy(): void {
    this.assetRequest?.unsubscribe();
    this.sortUnsubscribe?.();
    this.resizeObserver?.disconnect();
    if (this.resizeDebounce) clearTimeout(this.resizeDebounce);
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
    if (!el || !root || root.adoptedStyleSheets.includes(MediaGridComponent.shadowSheet)) return;
    root.adoptedStyleSheets = [...root.adoptedStyleSheets, MediaGridComponent.shadowSheet, this.rowSheet];
    root.addEventListener('pointerover', MediaGridComponent.titleSortButton);
    root.addEventListener('pointerover', this.trackHover);
    el.addEventListener('pointerleave', () => this.hoverRow.set(null));
  }

  private readonly trackHover = (event: Event): void => {
    const cell = (event.target as Element | null)?.closest?.('.body-cell');
    this.hoverRow.set(cell?.getAttribute('data-row') ?? null);
  };

  /** The grid's options id, which also prefixes its default row keys. */
  private static readonly gridId = 'media-grid';

  /** Grid column name to the API's sort field, for the sortable columns. */
  private static readonly sortFields: Record<string, SortField> = {
    name: 'name', size: 'size', duration: 'duration', date: 'date',
  };

  /** The sort buttons are glyph-only. The grid gives them an aria-label
   *  ("Sort", "Sort ascending", ...) but no tooltip, and it rewrites the
   *  header on every sort, so the label is mirrored into a title at the
   *  moment the pointer reaches a button rather than once after render. */
  private static titleSortButton(event: Event): void {
    const button = (event.target as Element | null)?.closest?.('[data-action="sort"]');
    if (button instanceof HTMLElement) button.title = button.getAttribute('aria-label') ?? 'Sort';
  }

  private static readonly shadowSheet = (() => {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(`
      /* One line per row, at a fixed height. A wrapping cell would break
         the list's rhythm and hide the drawn quantity below the fold. */
      .body-cell {
        box-sizing: border-box;
        height: var(--app-grid-row-height, 52px);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        align-content: center;
      }
      /* Hover and selection cover the whole row (see rowSheet), so the
         library's single-cell outline on hover goes. */
      .body-cell:hover:not(.row-focused):not(.cell-focused):not(.cell-editing),
      .body-grid > .body-cell:hover:not(.row-focused):not(.cell-focused):not(.cell-editing) {
        box-shadow: none;
      }
      /* A click leaves no lasting mark on the row; keyboard focus still
         shows as a ring on the focused cell. */
      .body-cell.ui-grid-row-focused { outline: none; }
      .body-cell:focus-visible {
        outline: 2px solid var(--ui-grid-accent);
        outline-offset: -2px;
      }
      /* Cell templates are slotted in from the light DOM, and the wrapper
         they land in is a shrink-wrapping flex item. Without this the
         drawn quantity has no width to be drawn across. */
      .cell-shell { min-width: 0; }
      .cell-content { flex: 1 1 auto; min-width: 0; }
      ::slotted(span) { display: block; width: 100%; min-width: 0; }
      .header-cell {
        box-sizing: border-box;
        height: var(--app-grid-header-height, 44px);
      }
      /* Column heads: Immich's muted medium label, sentence case. */
      .header-label {
        font-size: 13px;
        font-weight: 500;
        color: var(--ui-grid-muted-color);
        text-overflow: ellipsis;
      }
      .header-cell.is-active .header-label { color: var(--ui-grid-cell-color); }
      /* Unsorted marks stay faint until the column is pointed at. */
      .header-action[data-action="sort"] { transition: background 150ms ease, opacity 150ms ease; }
      .header-cell:not(.is-active) .header-action[data-action="sort"] { opacity: 0.45; }
      .header-cell:hover .header-action[data-action="sort"] { opacity: 1; }
      .header-action[data-action="sort"]:hover { background: var(--immich-hover); }
      .empty-state { color: var(--ui-grid-muted-color); max-width: none; }
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
      captureDates: s.captureDates(), metadata: s.metadata(),
    }).subscribe({
      next: data => {
        s.videos.set(data.assets ?? []);
        s.total.set(data.total ?? 0);
        s.totalSize.set(data.total_size ?? 0);
        s.totalPotential.set(data.total_potential ?? 0);
        s.perPage.set(data.per_page ?? s.perPage());
        s.loaded.set(true);
        s.loadError.set(data.error ?? null);
        this.hoverRow.set(null);
        s.loading.set(false);
        this.pageLoading.set(false);
        this.loadGen.update(g => g + 1);
      },
      error: (response: { error?: { error?: string } }) => {
        // Surface validation and Immich compatibility errors from the backend.
        s.loadError.set(response.error?.error
          ?? 'Could not reach the backend to list assets. Check that Immich is up and the API key is still valid, then try again.');
        s.videos.set([]);
        s.clearSelection();
        s.total.set(0);
        s.totalSize.set(0);
        s.totalPotential.set(0);
        s.loaded.set(true);
        s.loading.set(false);
        this.pageLoading.set(false);
        this.hoverRow.set(null);
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

  formatCaptureDate(value: unknown): string {
    if (!value) return '—';
    const parsed = new Date(String(value));
    if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 10);
    try {
      return new Intl.DateTimeFormat(undefined, {
        timeZone: this.store.captureTimezone(), year: 'numeric', month: '2-digit', day: '2-digit',
      }).format(parsed);
    } catch {
      return String(value).slice(0, 10);
    }
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
