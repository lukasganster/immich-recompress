import { computed, effect, Injectable, signal } from '@angular/core';
import {
  Capabilities, EncoderInfo, JobPublic, JobStatus, KeyOwner, MediaType, ProcessedEntry,
  Settings, SortField, SortOrder, SseJobUpdate, User, VideoSummary,
} from '../models/api.models';

const LS_BROWSE = 'immich_browse';
const LS_PROCESSED = 'immich_processed';
const LS_SETTINGS = 'immich_settings';
const PER_PAGE_OPTIONS = [10, 25, 50, 100] as const;

const BUSY: ReadonlySet<JobStatus> = new Set(['downloading', 'encoding', 'replacing']);
const TERMINAL: ReadonlySet<JobStatus> = new Set(['done', 'downloaded', 'encoded', 'skipped', 'error', 'cancelled', 'discarded']);
/** Already-optimized success states — not eligible for (re-)selection. */
const DONE: ReadonlySet<JobStatus> = new Set(['done', 'downloaded', 'encoded', 'processed']);

const PRESET_SAVINGS_MULT: Record<string, number> = {
  ultrafast: 0.62, superfast: 0.70, veryfast: 0.80, faster: 0.90,
  fast: 0.95, medium: 1.0, slow: 1.05, slower: 1.08, veryslow: 1.10,
};
const PRESET_REALTIME: Record<string, number> = {
  ultrafast: 0.15, superfast: 0.22, veryfast: 0.35, faster: 0.55,
  fast: 0.8, medium: 1.3, slow: 2.6, slower: 4.2, veryslow: 7.5,
};

@Injectable({ providedIn: 'root' })
export class StoreService {
  // --- media list ---
  readonly videos = signal<VideoSummary[]>([]);
  readonly total = signal(0);
  readonly totalSize = signal(0);
  readonly totalPotential = signal(0);
  readonly page = signal(1);
  readonly perPage = signal<number>(100);
  readonly sort = signal<SortField>('size');
  readonly order = signal<SortOrder>('desc');
  readonly codec = signal('');
  readonly userFilter = signal('');
  readonly search = signal('');
  readonly loaded = signal(false);
  readonly loadError = signal<string | null>(null);
  /** True while a Select Media asset load is in flight (drives the loading dialog). */
  readonly loading = signal(false);
  /** Escalating reassurance text shown when a fetch runs long ('' = none). */
  readonly loadingHint = signal('');
  private loadingHintTimers: ReturnType<typeof setTimeout>[] = [];

  // --- media type / thresholds ---
  readonly media = signal<MediaType>('video');
  readonly videoMinGb = signal(0.5);
  readonly photoMinMb = signal(10);

  // --- users ---
  readonly users = signal<User[]>([]);

  // --- API-key owners / library selection (empty selectedKeys = scan all) ---
  readonly keyOwners = signal<KeyOwner[]>([]);
  readonly selectedKeys = signal<number[]>([]);

  // --- immich info ---
  readonly immichUrl = signal('');
  readonly immichVersion = signal('');
  readonly handbrake = signal(false);
  readonly ffprobe = signal(false);
  readonly ffmpeg = signal(false);

  // --- encoder/CPU capabilities (from /api/capabilities) ---
  readonly encoders = signal<EncoderInfo[]>([]);
  readonly cpuCount = signal(1);
  /** The encoder spec for the current settings, or undefined until caps load. */
  readonly selectedEncoder = computed(() =>
    this.encoders().find(e => e.id === this.settings().encoder));

  // --- queue ---
  readonly jobs = signal<Record<string, JobPublic>>({});
  readonly activeId = signal<string | null>(null);
  readonly savedBytes = signal(0);

  // --- selection ---
  readonly selected = signal<Set<string>>(new Set());

  // --- processed history (localStorage) ---
  readonly processed = signal<Record<string, ProcessedEntry>>({});

  // --- settings ---
  readonly settings = signal<Settings>({
    media: 'video',
    encoder: 'x265',
    quality: 24,
    threads: 0,  // 0 = use all cores; set to the detected max once caps load
    photo_target_savings: 40,
    compress_raw: false,
    preset: 'medium',
    resolution: 'original',
    motion_action: 'remove',
    skip_codecs: 'hevc,av1',
    min_savings: 10,
    replace: true,
    confirm: true,
  });

  // --- derived ---
  readonly perPageOptions = PER_PAGE_OPTIONS;

  readonly effectiveMinMb = computed(() =>
    this.media() === 'video'
      ? Math.round(this.videoMinGb() * 1024)
      : this.photoMinMb()
  );

  readonly selectedVideos = computed(() => {
    const sel = this.selected();
    return this.videos().filter(v => sel.has(v.id));
  });

  readonly selectionBytes = computed(() =>
    this.selectedVideos().reduce((s, v) => s + (v.size || 0), 0)
  );

  readonly estimatedSavingsFraction = computed(() => {
    const s = this.settings();
    if (this.media() === 'motionphoto') return 1;
    if (this.media() === 'image') return s.photo_target_savings / 100;
    const anchors: [number, number][] = [
      [18, 0.20], [20, 0.30], [22, 0.38], [24, 0.45],
      [26, 0.52], [28, 0.60], [30, 0.66], [32, 0.72],
    ];
    let q = Math.max(18, Math.min(32, s.quality));
    let frac = anchors[0][1];
    for (let i = 0; i < anchors.length - 1; i++) {
      const [q1, f1] = anchors[i], [q2, f2] = anchors[i + 1];
      if (q >= q1 && q <= q2) { frac = f1 + (f2 - f1) * ((q - q1) / (q2 - q1)); break; }
      if (q > q2) frac = f2;
    }
    // Software encoders gain efficiency from slower presets; hardware encoders
    // ignore presets and trade a little ratio for speed.
    const mult = this.selectedEncoder()?.hw ? 0.9 : (PRESET_SAVINGS_MULT[s.preset] ?? 1.0);
    return Math.max(0, Math.min(0.95, frac * mult));
  });

  readonly estimatedSavingsBytes = computed(() =>
    Math.round(this.selectionBytes() * this.estimatedSavingsFraction())
  );

  readonly estimatedSeconds = computed(() => {
    const s = this.settings();
    return this.selectedVideos().reduce((sum, v) => {
      if (v.media === 'motionphoto') return sum + 2;  // metadata-only, ~instant
      if (v.media === 'image') return sum + Math.max(1, (v.size / (1024 * 1024)) * 0.15);
      let dur = v.duration || (v.size * 8 / 10e6);
      let res = 1;
      if (v.resolution?.includes('x')) {
        const [w, h] = v.resolution.split('x').map(Number);
        if (w && h) res = Math.min(8, Math.max(0.3, (w * h) / (1920 * 1080)));
      }
      const rt = this.selectedEncoder()?.hw ? 0.15 : (PRESET_REALTIME[s.preset] ?? 1.3);
      return sum + dur * rt * res;
    }, 0);
  });

  readonly queueStats = computed(() => {
    const j = this.jobs();
    const s = { queued: 0, active: 0, review: 0, done: 0, skipped: 0, error: 0 };
    for (const id in j) {
      const st = j[id].status;
      if (st === 'queued') s.queued++;
      else if (BUSY.has(st)) s.active++;
      else if (st === 'review') s.review++;
      else if (st === 'done' || st === 'downloaded' || st === 'encoded') s.done++;
      else if (st === 'skipped') s.skipped++;
      else if (st === 'error') s.error++;
    }
    return s;
  });

  constructor() {
    this.loadFromStorage();

    // Escalating "still working" messages while a Select Media fetch runs long.
    // Re-runs whenever `loading` flips: starts the timers on true, clears on false.
    effect(() => {
      const loading = this.loading();
      this.loadingHintTimers.forEach(clearTimeout);
      this.loadingHintTimers = [];
      this.loadingHint.set('');
      if (!loading) return;
      this.loadingHintTimers.push(
        setTimeout(() => this.loadingHint.set('This takes a bit longer…'), 5000),
        setTimeout(() => this.loadingHint.set('Scanning will soon be completed…'), 10000),
        setTimeout(() => this.loadingHint.set('Request is still pending…'), 15000),
      );
    });
  }

  // --- localStorage ---
  loadFromStorage(): void {
    try {
      const b = JSON.parse(localStorage.getItem(LS_BROWSE) || '{}');
      if (b.media === 'image' || b.media === 'video' || b.media === 'motionphoto') this.media.set(b.media);
      if (Number.isFinite(+b.videoMinGb)) this.videoMinGb.set(+b.videoMinGb);
      if (Number.isFinite(+b.photoMinMb)) this.photoMinMb.set(+b.photoMinMb);
      if (([10, 25, 50, 100] as number[]).includes(+b.perPage)) this.perPage.set(+b.perPage);
      if (Array.isArray(b.selectedKeys)) {
        this.selectedKeys.set(b.selectedKeys.map(Number).filter((n: number) => Number.isInteger(n)));
      }
    } catch { /* ignore */ }
    try {
      this.processed.set(JSON.parse(localStorage.getItem(LS_PROCESSED) || '{}') || {});
    } catch { this.processed.set({}); }
    try {
      const saved = JSON.parse(localStorage.getItem(LS_SETTINGS) || '{}');
      if (saved && typeof saved === 'object') {
        // backup_dir is no longer user-configurable; drop any value persisted by
        // older versions so a stale path can't be sent on enqueue.
        delete saved.backup_dir;
        this.settings.update(s => ({ ...s, ...saved }));
      }
    } catch { /* ignore */ }
  }

  saveBrowse(): void {
    try {
      localStorage.setItem(LS_BROWSE, JSON.stringify({
        media: this.media(), videoMinGb: this.videoMinGb(),
        photoMinMb: this.photoMinMb(), perPage: this.perPage(),
        selectedKeys: this.selectedKeys(),
      }));
    } catch { /* ignore */ }
  }

  saveSettings(): void {
    try {
      localStorage.setItem(LS_SETTINGS, JSON.stringify(this.settings()));
    } catch { /* ignore */ }
  }

  /** Friendly label for an encoder id, from detected capabilities; falls back
   *  to the raw id (e.g. for a historical job whose encoder this build lacks). */
  encoderLabel(id: string): string {
    return this.encoders().find(e => e.id === id)?.label ?? id ?? 'HEVC';
  }

  /** Apply the runtime encoder/CPU capabilities: reconcile persisted settings
   *  with what the running HandBrake build actually supports. */
  applyCapabilities(caps: Capabilities): void {
    const encoders = caps.encoders ?? [];
    const cpu = Math.max(1, caps.cpu_count || 1);
    this.encoders.set(encoders);
    this.cpuCount.set(cpu);
    this.settings.update(s => {
      const next = { ...s };
      // Default/clamp the CPU-core count to the detected max (0 = unset → all).
      if (!next.threads || next.threads > cpu) next.threads = cpu;
      // If the persisted encoder isn't available in this build, fall back to the
      // first available one and reset quality to that encoder's default scale.
      if (encoders.length && !encoders.some(e => e.id === next.encoder)) {
        next.encoder = encoders[0].id;
        next.quality = encoders[0].qdefault;
      }
      return next;
    });
    this.saveSettings();
  }

  markProcessed(id: string, job: JobPublic): void {
    this.processed.update(p => ({
      ...p,
      [id]: {
        status: 'done',
        old_size: job.old_size,
        new_size: job.new_size,
        savings: job.savings,
        media: job.media,
        ts: Date.now(),
      },
    }));
    try { localStorage.setItem(LS_PROCESSED, JSON.stringify(this.processed())); } catch { /* ignore */ }
  }

  clearProcessed(): void {
    this.processed.set({});
    try { localStorage.removeItem(LS_PROCESSED); } catch { /* ignore */ }
  }

  // --- job helpers ---
  effectiveStatus(v: VideoSummary): JobStatus {
    const job = this.jobs()[v.id];
    if (job) return job.status;
    if (this.processed()[v.id]) return 'processed';
    return v.status || 'idle';
  }

  isBusy(status: JobStatus): boolean {
    return BUSY.has(status);
  }

  /** True for assets already optimized (done/encoded/processed) — locked from selection. */
  isDone(status: JobStatus): boolean {
    return DONE.has(status);
  }

  isTerminal(status: JobStatus): boolean {
    return TERMINAL.has(status);
  }

  applyJobUpdate(update: SseJobUpdate): void {
    this.jobs.update(jobs => {
      const existing = jobs[update.id] ?? ({} as JobPublic);
      const updated: JobPublic = {
        ...existing,
        id: update.id,
        status: update.status,
        progress: update.progress,
        log: update.log,
        new_size: update.new_size ?? existing.new_size,
        savings: update.savings ?? existing.savings,
      };
      if (update.status === 'done') {
        this.markProcessed(update.id, updated);
      }
      return { ...jobs, [update.id]: updated };
    });

    if (BUSY.has(update.status)) {
      this.activeId.set(update.id);
    } else if (this.activeId() === update.id) {
      this.activeId.set(null);
    }
  }

  // --- selection ---
  toggleSelect(id: string): void {
    this.selected.update(s => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }

  selectAll(ids: string[]): void {
    this.selected.update(s => {
      const n = new Set(s);
      for (const id of ids) n.add(id);
      return n;
    });
  }

  clearSelection(): void {
    this.selected.set(new Set());
  }

  /** Merge assets resolved by link/ID into the list (newest first), mark the
   *  list as loaded, and pre-select them so they're ready to enqueue. */
  addResolved(summaries: VideoSummary[]): void {
    if (!summaries.length) return;
    const ids = new Set(summaries.map(s => s.id));
    this.videos.update(list => [...summaries, ...list.filter(v => !ids.has(v.id))]);
    this.loaded.set(true);
    this.total.set(this.videos().length);
    this.selectAll([...ids]);
  }
}

export { BUSY };
