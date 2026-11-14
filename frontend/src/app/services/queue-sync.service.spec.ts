import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { JobsResponse } from '../models/api.models';
import { QueueSyncService } from './queue-sync.service';
import { StoreService } from './store.service';

function snapshot(concurrent: number): JobsResponse {
  return {
    active: null, active_ids: [], concurrent_jobs: concurrent, queue: [], jobs: {},
    stats: { queued: 0, active: 0, review: 0, done: 0, skipped: 0, error: 0, saved_bytes: 0 },
  };
}

describe('QueueSyncService', () => {
  beforeEach(() => TestBed.configureTestingModule({
    providers: [provideHttpClient(), provideHttpClientTesting()],
  }));
  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('ignores a slow snapshot that a newer request already superseded', () => {
    const sync = TestBed.inject(QueueSyncService);
    const store = TestBed.inject(StoreService);
    const http = TestBed.inject(HttpTestingController);
    sync.refreshJobs();
    sync.refreshJobs();
    const [older, newer] = http.match('/api/jobs');
    newer.flush(snapshot(3));
    older.flush(snapshot(1));
    expect(store.concurrentJobs()).toBe(3);
  });

  it('reports failure so the caller can show the saved value again', () => {
    const sync = TestBed.inject(QueueSyncService);
    const results: boolean[] = [];
    sync.saveConcurrentJobs(4).subscribe(saved => results.push(saved));
    expect(sync.savingConcurrency()).toBe(true);
    TestBed.inject(HttpTestingController).expectOne('/api/settings')
      .flush({ error: 'unavailable' }, { status: 503, statusText: 'Unavailable' });
    expect(results).toEqual([false]);
    expect(sync.savingConcurrency()).toBe(false);
    expect(sync.concurrencyError()).toContain('Could not save');
  });
});
