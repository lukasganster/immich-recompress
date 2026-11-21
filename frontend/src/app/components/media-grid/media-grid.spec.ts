import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { StoreService } from '../../services/store.service';
import { MediaGridComponent } from './media-grid';

describe('media grid format filter', () => {
  let fixture: ComponentFixture<MediaGridComponent>;
  let store: StoreService;
  let http: HttpTestingController;

  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    TestBed.configureTestingModule({
      imports: [MediaGridComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    fixture = TestBed.createComponent(MediaGridComponent);
    store = TestBed.inject(StoreService);
    http = TestBed.inject(HttpTestingController);
    store.media.set('image');
  });

  afterEach(() => {
    http.verify();
    vi.unstubAllGlobals();
  });

  it('sends the chosen formats and keeps the counts and unchecked total', () => {
    store.formatFilter.set(['TIFF']);
    fixture.componentInstance.load();
    const request = http.expectOne(r => r.url === '/api/assets');
    expect(request.request.params.getAll('format')).toEqual(['TIFF']);
    request.flush({ assets: [], total: 0, formats: { TIFF: 2, DNG: 1 }, jxl_unchecked: 3 });
    expect(store.formats()).toEqual({ TIFF: 2, DNG: 1 });
    expect(store.jxlUnchecked()).toBe(3);
    fixture.componentInstance.load();
    http.expectOne(r => r.url === '/api/assets').flush({ assets: [], total: 0 });
    expect(store.jxlUnchecked()).toBe(0);
  });

  it('sends only the size range for Live Photos', () => {
    store.media.set('motionphoto');
    store.photoMaxMb.set(10);
    store.photoMinMp.set(5);
    store.photoMaxMp.set(20);
    store.jxlLosslessOnly.set(true);
    store.formatFilter.set(['HEIC']);
    fixture.componentInstance.load();
    const request = http.expectOne(r => r.url === '/api/assets');
    expect(request.request.params.get('max_mb')).toBe('10');
    for (const key of ['min_mp', 'max_mp', 'jxl_lossless', 'format']) {
      expect(request.request.params.has(key)).toBe(false);
    }
    request.flush({ assets: [], total: 0, formats: { HEIC: 1 } });
  });

  it('rescans only when asked and keeps the scan time', () => {
    fixture.componentInstance.load(true);
    const rescan = http.expectOne(r => r.url === '/api/assets');
    expect(rescan.request.params.get('refresh')).toBe('1');
    rescan.flush({ assets: [], total: 0, scanned_at: '2026-11-21T10:00:00Z' });
    expect(store.scannedAt()).toBe('2026-11-21T10:00:00Z');
    fixture.componentInstance.load();
    const reuse = http.expectOne(r => r.url === '/api/assets');
    expect(reuse.request.params.has('refresh')).toBe(false);
    reuse.flush({ assets: [], total: 0, scanned_at: '2026-11-21T10:00:00Z' });
  });
});
