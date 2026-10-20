import { TestBed } from '@angular/core/testing';
import { SettingsPanelComponent } from './settings-panel';
import { StoreService } from '../../services/store.service';

describe('Photo output settings', () => {
  beforeEach(() => {
    for (const key of ['immich_browse', 'immich_processed', 'immich_settings']) {
      try { localStorage.removeItem(key); } catch { /* not available here */ }
    }
  });

  it('switches to lossless JPEG XL without showing JPEG quality or RAW controls', async () => {
    TestBed.configureTestingModule({ imports: [SettingsPanelComponent] });
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    const store = TestBed.inject(StoreService);
    store.jpegXlAvailable.set(true);
    fixture.componentInstance.open.set(true);
    fixture.detectChanges();
    await fixture.whenStable();
    const element: HTMLElement = fixture.nativeElement;
    const select = element.querySelector<HTMLSelectElement>('#photoFormat')!;
    expect(select.value).toBe('jpeg');
    expect(element.querySelector('#compressRaw')).not.toBeNull();

    select.value = 'jxl';
    select.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    fixture.detectChanges();

    expect(store.settings().photo_format).toBe('jxl');
    expect(element.querySelector('#compressRaw')).toBeNull();
    expect(element.textContent).not.toContain('Target Savings:');
    expect(element.textContent).toContain('Savings vary by photo');
    expect(fixture.componentInstance.photoSummary).toBe('JPEG XL · lossless');
  });

  it('explains the missing tools and disables unavailable JPEG XL output', () => {
    TestBed.configureTestingModule({ imports: [SettingsPanelComponent] });
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    fixture.componentInstance.open.set(true);
    fixture.detectChanges();
    const element: HTMLElement = fixture.nativeElement;
    expect(element.querySelector<HTMLOptionElement>('option[value="jxl"]')!.disabled).toBe(true);
    expect(element.textContent).toContain('Install libjxl-tools on the server');
  });

  it('explains how to recover when a saved JPEG XL choice becomes unavailable', () => {
    TestBed.configureTestingModule({ imports: [SettingsPanelComponent] });
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    const store = TestBed.inject(StoreService);
    store.settings.update(s => ({ ...s, photo_format: 'jxl' }));
    store.applyCapabilities({ encoders: [], cpu_count: 1, jpeg_xl: false });
    fixture.componentInstance.open.set(true);
    fixture.detectChanges();
    const element: HTMLElement = fixture.nativeElement;
    expect(element.querySelector('[role="alert"]')!.textContent).toContain(
      'Choose JPEG or install libjxl-tools');
    expect(store.settings().photo_format).toBe('jxl');
  });
});
