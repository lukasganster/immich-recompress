import { TestBed } from '@angular/core/testing';
import { StoreService } from './store.service';
import { JobPublic, VideoSummary } from '../models/api.models';

/** Build a full VideoSummary from a partial, so tests stay readable. */
function vs(partial: Partial<VideoSummary> & { id: string }): VideoSummary {
  return {
    media: 'video', name: partial.id, size: 0, size_human: '', potential: 0,
    duration: 0, duration_human: '', codec: '', resolution: '', bitrate: null,
    date: null, owner_id: '', owner_name: '', is_favorite: false,
    is_archived: false, albums: [], people: [], status: 'idle',
    ...partial,
  };
}

describe('StoreService', () => {
  let store: StoreService;

  beforeEach(() => {
    // Reset persisted keys so each test starts from defaults. (Some test
    // environments expose a localStorage without a working clear().)
    for (const k of ['immich_browse', 'immich_processed', 'immich_settings']) {
      try { localStorage.removeItem(k); } catch { /* not available here */ }
    }
    TestBed.configureTestingModule({});
    store = TestBed.inject(StoreService);
  });

  it('is created', () => {
    expect(store).toBeTruthy();
  });

  it('effectiveMinMb converts GB→MB for video and passes MB through for images', () => {
    store.media.set('video');
    store.videoMinGb.set(0.5);
    expect(store.effectiveMinMb()).toBe(512);

    store.media.set('image');
    store.photoMinMb.set(8);
    expect(store.effectiveMinMb()).toBe(8);
  });

  it('toggleSelect adds then removes, and selectionBytes tracks selected sizes', () => {
    store.videos.set([vs({ id: 'a', size: 1000 }), vs({ id: 'b', size: 250 })]);

    store.toggleSelect('a');
    expect(store.selected().has('a')).toBe(true);
    expect(store.selectionBytes()).toBe(1000);

    store.toggleSelect('b');
    expect(store.selectionBytes()).toBe(1250);

    store.toggleSelect('a');
    expect(store.selected().has('a')).toBe(false);
    expect(store.selectionBytes()).toBe(250);
  });

  it('queueStats counts jobs by status bucket', () => {
    const jobs: Record<string, JobPublic> = {
      a: { status: 'queued' } as JobPublic,
      b: { status: 'encoding' } as JobPublic,   // BUSY -> active
      c: { status: 'review' } as JobPublic,
      d: { status: 'done' } as JobPublic,
      e: { status: 'encoded' } as JobPublic,    // counts as done
      f: { status: 'skipped' } as JobPublic,
      g: { status: 'error' } as JobPublic,
    };
    store.jobs.set(jobs);
    const s = store.queueStats();
    expect(s).toEqual({ queued: 1, active: 1, review: 1, done: 2, skipped: 1, error: 1 });
  });

  it('estimatedSavingsFraction uses photo_target_savings for image media', () => {
    store.media.set('image');
    store.settings.update(s => ({ ...s, photo_target_savings: 40 }));
    expect(store.estimatedSavingsFraction()).toBeCloseTo(0.4, 5);
  });

  it('applyJobUpdate stores the job and tracks the active id', () => {
    store.applyJobUpdate({
      id: 'x', status: 'encoding', progress: 0.5, log: 'working',
      new_size: null, savings: null,
    });
    expect(store.jobs()['x'].status).toBe('encoding');
    expect(store.activeId()).toBe('x');

    // A terminal update for the active job clears the active id.
    store.applyJobUpdate({
      id: 'x', status: 'done', progress: 1, log: 'done',
      new_size: 500, savings: 0.5,
    });
    expect(store.activeId()).toBeNull();
    expect(store.processed()['x']).toBeTruthy();
  });

  it('effectiveStatus prefers a live job, then processed history, then the asset status', () => {
    const v = vs({ id: 'a', status: 'idle' });
    expect(store.effectiveStatus(v)).toBe('idle');

    store.markProcessed('a', { status: 'done' } as JobPublic);
    expect(store.effectiveStatus(v)).toBe('processed');

    store.jobs.set({ a: { status: 'encoding' } as JobPublic });
    expect(store.effectiveStatus(v)).toBe('encoding');
  });
});
