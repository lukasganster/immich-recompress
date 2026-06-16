import { Injectable, OnDestroy, signal } from '@angular/core';
import { Subject } from 'rxjs';
import { SseJobUpdate } from '../models/api.models';

@Injectable({ providedIn: 'root' })
export class EventsService implements OnDestroy {
  readonly connected = signal(false);
  readonly jobUpdate$ = new Subject<SseJobUpdate>();
  readonly queueUpdate$ = new Subject<void>();

  private es: EventSource | null = null;
  private reconnectDelay = 3000;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private destroyed = false;

  connect(): void {
    if (this.es) return;
    this.doConnect();
  }

  private doConnect(): void {
    if (this.destroyed) return;
    try {
      this.es = new EventSource('/api/events');
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.es.addEventListener('connected', () => {
      this.connected.set(true);
      this.reconnectDelay = 3000;
    });
    this.es.addEventListener('job_update', (e: MessageEvent) => {
      try {
        const d: SseJobUpdate = JSON.parse(e.data);
        this.jobUpdate$.next(d);
      } catch { /* ignore */ }
    });
    this.es.addEventListener('queue_update', () => {
      this.queueUpdate$.next();
    });
    this.es.onerror = () => {
      this.connected.set(false);
      this.es?.close();
      this.es = null;
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.destroyed) return;
    this.reconnectTimer = setTimeout(() => this.doConnect(), this.reconnectDelay);
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.es?.close();
    this.es = null;
  }
}
