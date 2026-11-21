import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ApiService } from './api.service';

describe('ApiService browse requests', () => {
  it('serializes all composed filters and repeated person IDs', () => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    const api = TestBed.inject(ApiService);
    const http = TestBed.inject(HttpTestingController);
    api.assets({
      page: 2, per_page: 25, sort: 'size', order: 'desc', media: 'image', min_mb: 10,
      keys: [0, 1], user: 'user-a', search: 'holiday',
      captureDates: { dateFrom: '2026-01-01', dateTo: '2026-02-01', timezone: 'Europe/Vienna' },
      metadata: { albumId: 'album-a', personIds: ['person-a', 'person-b'], city: 'Vienna', state: 'Vienna', country: 'Austria' },
    }).subscribe();
    const request = http.expectOne(r => r.url === '/api/assets');
    expect(request.request.params.getAll('person_id')).toEqual(['person-a', 'person-b']);
    for (const [key, value] of Object.entries({ album_id: 'album-a', keys: '0,1', city: 'Vienna', state: 'Vienna', country: 'Austria',
      user: 'user-a', search: 'holiday', date_from: '2026-01-01', date_to: '2026-02-01', timezone: 'Europe/Vienna', page: '2' })) {
      expect(request.request.params.get(key)).toBe(value);
    }
    request.flush({ assets: [], total: 0 });
    http.verify();
  });

  it('sends photo ranges, formats and the lossless flag only when set', () => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    const api = TestBed.inject(ApiService);
    const http = TestBed.inject(HttpTestingController);
    const base = { page: 1, per_page: 25, sort: 'size', order: 'desc', media: 'image', min_mb: 2 } as const;
    api.assets({ ...base, max_mb: 10, min_mp: 5, max_mp: null, formats: ['TIFF', 'DNG'], jxl_lossless: true }).subscribe();
    const request = http.expectOne(r => r.url === '/api/assets');
    expect(request.request.params.get('max_mb')).toBe('10');
    expect(request.request.params.get('min_mp')).toBe('5');
    expect(request.request.params.getAll('format')).toEqual(['TIFF', 'DNG']);
    expect(request.request.params.get('jxl_lossless')).toBe('1');
    expect(request.request.params.has('max_mp')).toBe(false);
    request.flush({ assets: [], total: 0 });
    api.assets({ ...base, max_mb: null, min_mp: null, formats: [], jxl_lossless: false }).subscribe();
    const bare = http.expectOne(r => r.url === '/api/assets');
    for (const key of ['max_mb', 'min_mp', 'max_mp', 'format', 'jxl_lossless']) {
      expect(bare.request.params.has(key)).toBe(false);
    }
    bare.flush({ assets: [], total: 0 });
    http.verify();
  });
});
