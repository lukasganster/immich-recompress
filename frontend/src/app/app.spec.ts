import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { App } from './app';
import { SettingsPanelComponent } from './components/settings-panel/settings-panel';
import { StoreService } from './services/store.service';

describe('JPEG XL preflight', () => {
  it('opens settings instead of queuing photos when saved JPEG XL is unavailable', () => {
    TestBed.configureTestingModule({
      imports: [App], providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    TestBed.overrideComponent(App, { set: {
      template: '<app-settings-panel></app-settings-panel>', imports: [SettingsPanelComponent],
    } });
    const fixture = TestBed.createComponent(App);
    vi.spyOn(fixture.componentInstance, 'ngOnInit').mockImplementation(() => {});
    const store = TestBed.inject(StoreService);
    store.media.set('image');
    store.settings.update(s => ({ ...s, photo_format: 'jxl' }));
    store.jpegXlAvailable.set(false);
    fixture.detectChanges();

    fixture.componentInstance.onEncode(['photo']);
    fixture.detectChanges();

    TestBed.inject(HttpTestingController).expectNone('/api/enqueue');
    const element: HTMLElement = fixture.nativeElement;
    expect(element.querySelector('[role="alert"]')!.textContent).toContain(
      'Choose JPEG or install libjxl-tools');
  });
});

describe('fetch subtitle', () => {
  it('shows photo ranges with en dashes and open bounds', () => {
    TestBed.configureTestingModule({
      imports: [App], providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    const app = TestBed.createComponent(App).componentInstance;
    const store = TestBed.inject(StoreService);
    store.media.set('image');
    store.photoMinMb.set(1);
    store.photoMaxMb.set(10);
    store.photoMinMp.set(5);
    expect(app.loadingSubtitle).toBe('Photos · 1–10 MB · ≥ 5 MP');
    store.photoMaxMb.set(null);
    store.photoMinMp.set(null);
    store.photoMaxMp.set(20);
    store.jxlLosslessOnly.set(true);
    expect(app.loadingSubtitle).toBe('Photos · ≥ 1 MB · ≤ 20 MP · lossless JPEG XL');
    store.media.set('motionphoto');
    expect(app.loadingSubtitle).toBe('Live Photos · ≥ 1 MB');
  });
});
