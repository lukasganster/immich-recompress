import { inject, Injectable, signal } from '@angular/core';
import { catchError, EMPTY, finalize, map, Observable, of } from 'rxjs';
import { ApiService } from './api.service';
import { StoreService } from './store.service';

const MAX_CONCURRENT_JOBS = 8;

/** Keeps the store in step with state the server owns: the job snapshot and
 *  server-wide settings shared by every browser session. */
@Injectable({ providedIn: 'root' })
export class QueueSyncService {
  private readonly api = inject(ApiService);
  private readonly store = inject(StoreService);

  readonly savingConcurrency = signal(false);
  readonly concurrencyError = signal('');

  /** Only the newest snapshot request applies, so a slow reply cannot roll
   *  the queue back to an older state. Not rendered, so not a signal. */
  private latestJobsRequest = 0;

  refreshJobs(): void {
    const request = ++this.latestJobsRequest;
    this.api.jobs().subscribe({
      next: data => {
        if (request === this.latestJobsRequest) this.store.applyJobsSnapshot(data);
      },
      error: () => {
        if (request === this.latestJobsRequest) {
          this.store.serverSettingsError.set('Could not load server settings. Reload to retry.');
        }
      },
    });
  }

  /** Save the server-wide limit. Emits true once the server confirmed it and
   *  false when saving failed. Invalid input or a save already in flight emit
   *  nothing; invalid input sets `concurrencyError`. */
  saveConcurrentJobs(value: number | null): Observable<boolean> {
    if (value === null || !Number.isInteger(value) || value < 1 || value > MAX_CONCURRENT_JOBS) {
      this.concurrencyError.set(`Enter a whole number from 1 to ${MAX_CONCURRENT_JOBS}.`);
      return EMPTY;
    }
    if (this.savingConcurrency()) return EMPTY;
    this.concurrencyError.set('');
    this.savingConcurrency.set(true);
    return this.api.setConcurrentJobs(value).pipe(
      map(data => {
        this.store.concurrentJobs.set(data.concurrent_jobs);
        return true;
      }),
      catchError(() => {
        this.concurrencyError.set('Could not save concurrent jobs. Check the connection and retry.');
        return of(false);
      }),
      finalize(() => this.savingConcurrency.set(false)),
    );
  }
}
