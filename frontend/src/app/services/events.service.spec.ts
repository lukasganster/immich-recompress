import { TestBed } from '@angular/core/testing';
import { EventsService } from './events.service';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly listeners = new Map<string, () => void>();
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(_url: string) { FakeEventSource.instances.push(this); }
  addEventListener(name: string, listener: () => void): void { this.listeners.set(name, listener); }
  emit(name: string): void { this.listeners.get(name)?.(); }
}

describe('Live queue recovery', () => {
  let events: EventsService;
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource);
    events = TestBed.inject(EventsService);
  });
  afterEach(() => {
    events.ngOnDestroy();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('refreshes the queue and global settings after reconnecting', () => {
    const refresh = vi.fn();
    events.queueUpdate$.subscribe(refresh);
    events.connect();
    const original = FakeEventSource.instances[0];
    original.emit('connected');
    expect(refresh).toHaveBeenCalledTimes(1);
    original.onerror?.();
    expect(events.connected()).toBe(false);
    expect(original.close).toHaveBeenCalled();
    vi.advanceTimersByTime(3000);
    FakeEventSource.instances[1].emit('connected');
    expect(events.connected()).toBe(true);
    expect(refresh).toHaveBeenCalledTimes(2);
  });
});
