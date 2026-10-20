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
