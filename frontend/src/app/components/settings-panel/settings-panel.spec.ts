import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { SettingsPanelComponent } from './settings-panel';
import { StoreService } from '../../services/store.service';

describe('Photo output settings', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    for (const key of ['immich_browse', 'immich_processed', 'immich_settings']) {
      try { localStorage.removeItem(key); } catch { /* not available here */ }
    }
  });

  it('switches to lossless JPEG XL without showing JPEG quality controls', async () => {
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
    expect(element.querySelector('#compressRaw')).not.toBeNull();
    expect(element.querySelector('#setJpegChroma')).toBeNull();
    expect(element.textContent).not.toContain('Target Savings:');
    expect(element.textContent).toContain('Savings vary by photo');
    expect(fixture.componentInstance.photoSummary).toBe('JPEG XL · lossless');
  });

  it('sets a fractional lossy distance and restores the lossless setting', async () => {
    TestBed.configureTestingModule({ imports: [SettingsPanelComponent] });
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    const store = TestBed.inject(StoreService);
    store.jpegXlAvailable.set(true);
    store.settings.update(s => ({ ...s, photo_format: 'jxl' }));
    fixture.componentInstance.open.set(true);
    fixture.detectChanges();
    await fixture.whenStable();
    const element: HTMLElement = fixture.nativeElement;
    const input = element.querySelector<HTMLInputElement>('input[aria-label="JPEG XL distance value"]')!;
    input.value = '1.5';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    fixture.detectChanges();
    expect(store.settings().photo_jxl_distance).toBe(1.5);
    expect(fixture.componentInstance.photoSummary).toBe('JPEG XL · lossy · distance 1.5');
    expect(element.querySelector('#setTargetSavings')).toBeNull();
    expect(element.textContent).toContain('Positive values use lossy compression');

    input.value = '0';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    fixture.detectChanges();
    expect(store.settings().photo_jxl_distance).toBe(0);
    expect(fixture.componentInstance.photoSummary).toBe('JPEG XL · lossless');
  });

  it('caps megapixels and picks the JPEG chroma', async () => {
    TestBed.configureTestingModule({ imports: [SettingsPanelComponent] });
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    const store = TestBed.inject(StoreService);
    fixture.componentInstance.open.set(true);
    fixture.detectChanges();
    await fixture.whenStable();
    const element: HTMLElement = fixture.nativeElement;
    const set = async (control: HTMLInputElement | HTMLSelectElement, value: string) => {
      control.value = value;
      control.dispatchEvent(new Event(control instanceof HTMLSelectElement ? 'change' : 'input'));
      await fixture.whenStable();
      fixture.detectChanges();
    };
    const maxMp = element.querySelector<HTMLInputElement>('#setMaxMp')!;
    const chroma = element.querySelector<HTMLSelectElement>('#setJpegChroma')!;
    expect(chroma.value).toBe('source');
    expect(Array.from(chroma.options, o => o.text))
      .toEqual(['Match source', '4:4:4 (full colour)', '4:2:0 (smaller)']);

    await set(maxMp, '12');
    await set(chroma, '444');
    expect(store.settings().photo_max_mp).toBe(12);
    expect(store.settings().photo_jpeg_chroma).toBe('444');
    expect(fixture.componentInstance.photoSummary).toBe('JPEG · −40% target · 4:4:4 · max 12 MP');

    await set(maxMp, '0');
    await set(chroma, 'source');
    expect(store.settings().photo_max_mp).toBeNull();
    expect(fixture.componentInstance.photoSummary).toBe('JPEG · −40% target');
  });

  it('restores the stored distance after clearing or entering out-of-range values', async () => {
    TestBed.configureTestingModule({ imports: [SettingsPanelComponent] });
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    const store = TestBed.inject(StoreService);
    store.jpegXlAvailable.set(true);
    store.settings.update(s => ({ ...s, photo_format: 'jxl', photo_jxl_distance: 25 }));
    fixture.componentInstance.open.set(true);
    fixture.detectChanges();
    await fixture.whenStable();
    const input = (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLInputElement>('input[aria-label="JPEG XL distance value"]')!;
    for (const [entered, expected] of [['30', 25], ['', 25], ['-1', 0]] as const) {
      input.value = entered;
      input.dispatchEvent(new Event('input'));
      await fixture.whenStable();
      fixture.detectChanges();
      input.dispatchEvent(new Event('blur'));
      await fixture.whenStable();
      expect(store.settings().photo_jxl_distance).toBe(expected);
      expect(input.value).toBe(String(expected));
    }
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

  it('says the motion setting also decides what replacing a Live Photo still does', () => {
    TestBed.configureTestingModule({ imports: [SettingsPanelComponent] });
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    fixture.componentInstance.show();
    fixture.detectChanges();
    const help = (fixture.nativeElement as HTMLElement).querySelector('#setMotion ~ small')!;
    expect(help.textContent).toContain('Photos tab');
    expect(help.textContent).toContain('trashes the motion video');
    expect(help.textContent).not.toContain('never modified');
  });

  it('starts every section closed and states its value in the summary line', () => {
    TestBed.configureTestingModule({ imports: [SettingsPanelComponent] });
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    fixture.componentInstance.show();
    fixture.detectChanges();
    const sections = [...(fixture.nativeElement as HTMLElement).querySelectorAll('details')];
    expect(sections.map(d => d.querySelector('summary')!.firstChild!.textContent!.trim()))
      .toEqual(['Video', 'Photo', 'Live Photo', 'General']);
    expect(sections.some(d => d.open)).toBe(false);
    expect(sections.map(d => d.querySelector('.disclosure-value')!.textContent)).toEqual([
      fixture.componentInstance.videoSummary,
      fixture.componentInstance.photoSummary,
      fixture.componentInstance.liveSummary,
      fixture.componentInstance.generalSummary,
    ]);
  });

  it('opens the photo section when a saved JPEG XL choice cannot run, and keeps it open', async () => {
    TestBed.configureTestingModule({ imports: [SettingsPanelComponent] });
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    const store = TestBed.inject(StoreService);
    store.settings.update(s => ({ ...s, photo_format: 'jxl' }));
    fixture.componentInstance.show();
    fixture.detectChanges();
    const element: HTMLElement = fixture.nativeElement;
    const photo = element.querySelector<HTMLDetailsElement>('#photoFormat')!.closest('details')!;
    expect(photo.open).toBe(true);
    expect(photo.querySelector('.disclosure-value')!.textContent).toBe('JPEG XL unavailable');

    fixture.componentInstance.update({ photo_format: 'jpeg' });
    fixture.detectChanges();
    await fixture.whenStable();
    expect(photo.open).toBe(true);
    expect(photo.querySelector('.disclosure-value')!.classList.contains('warn')).toBe(false);
  });
});

describe('RAW decoder hint', () => {
  beforeEach(() => TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] }));

  it('names the missing tools only when RAW decoding is unavailable', async () => {
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    const store = TestBed.inject(StoreService);
    fixture.componentInstance.open.set(true);
    for (const raw of [true, false]) {
      store.applyCapabilities({ encoders: [], cpu_count: 2, jpeg_xl: true, raw });
      fixture.detectChanges();
      await fixture.whenStable();
      const text = (fixture.nativeElement as HTMLElement).textContent!;
      expect(text).toContain('Converts RAW to the output format and replaces the original.');
      expect(text.includes("RAW decoding isn't available here.")).toBe(!raw);
    }
  });
});

describe('Compression settings summary', () => {
  beforeEach(() => TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] }));
  const summaryRow = (element: HTMLElement, term: string) => {
    const dt = [...element.querySelectorAll('dt')].find(d => d.textContent!.trim() === term);
    return dt?.nextElementSibling?.textContent!.trim();
  };
  const render = (skipCodecs: string) => {
    TestBed.configureTestingModule({ imports: [SettingsPanelComponent] });
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    TestBed.inject(StoreService).settings.update(s => ({ ...s, skip_codecs: skipCodecs }));
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  };

  it('lists the codecs that will be skipped', () => {
    expect(summaryRow(render('hevc, av1,'), 'Skip')).toBe('HEVC, AV1');
  });

  it('leaves the row out when nothing is skipped', () => {
    expect(summaryRow(render(' , '), 'Skip')).toBeUndefined();
  });
});

describe('Server-wide concurrency', () => {
  beforeEach(() => TestBed.configureTestingModule({
    imports: [SettingsPanelComponent],
    providers: [provideHttpClient(), provideHttpClientTesting()],
  }));
  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('edits the global limit in General and waits for server confirmation', async () => {
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    const store = TestBed.inject(StoreService);
    store.concurrentJobs.set(2);
    store.serverSettingsLoaded.set(true);
    fixture.componentInstance.show();
    await fixture.whenStable();
    const element: HTMLElement = fixture.nativeElement;
    const input = element.querySelector<HTMLInputElement>('#setConcurrentJobs')!;
    expect(input.closest('details')!.querySelector('summary')!.textContent).toContain('General');
    expect(input.min).toBe('1');
    expect(input.max).toBe('8');
    const increase = element.querySelector<HTMLButtonElement>('[aria-label="Increase concurrent jobs"]')!;
    increase.click();
    await fixture.whenStable();
    const request = TestBed.inject(HttpTestingController).expectOne('/api/settings');
    expect(request.request.method).toBe('PUT');
    expect(request.request.body).toEqual({ concurrent_jobs: 3 });
    expect(store.concurrentJobs()).toBe(2);
    expect(input.disabled).toBe(true);
    request.flush({ concurrent_jobs: 3 });
    await fixture.whenStable();
    expect(input.value).toBe('3');
    expect(input.disabled).toBe(false);
    expect(element.textContent).toContain('Applies server-wide to all browser sessions');
    expect(store.settings()).not.toHaveProperty('concurrent_jobs');
  });

  it('restores the saved value and exposes an error when saving fails', async () => {
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    const store = TestBed.inject(StoreService);
    store.serverSettingsLoaded.set(true);
    fixture.componentInstance.show();
    await fixture.whenStable();
    const input = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>('#setConcurrentJobs')!;
    input.value = '4';
    input.dispatchEvent(new Event('input'));
    TestBed.inject(HttpTestingController).expectOne('/api/settings')
      .flush({ error: 'unavailable' }, { status: 503, statusText: 'Unavailable' });
    await fixture.whenStable();
    expect(input.value).toBe('1');
    expect(store.concurrentJobs()).toBe(1);
    expect(fixture.nativeElement.textContent).toContain('Could not save concurrent jobs');
    expect(input.getAttribute('aria-invalid')).toBe('true');
  });

  it('does not restore the old value when saving disables the focused input', async () => {
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    const store = TestBed.inject(StoreService);
    store.serverSettingsLoaded.set(true);
    fixture.componentInstance.show();
    await fixture.whenStable();
    const input = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>('#setConcurrentJobs')!;
    input.focus();
    input.value = '8';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new Event('blur'));
    expect(fixture.componentInstance.concurrencyDraft()).toBe(8);
    TestBed.inject(HttpTestingController).expectOne('/api/settings').flush({ concurrent_jobs: 8 });
    await fixture.whenStable();
    expect(input.value).toBe('8');
    expect(store.concurrentJobs()).toBe(8);
  });

  it('rejects invalid inputs locally and disables editing until settings load', async () => {
    const fixture = TestBed.createComponent(SettingsPanelComponent);
    fixture.componentInstance.show();
    await fixture.whenStable();
    const input = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>('#setConcurrentJobs')!;
    expect(input.disabled).toBe(true);
    for (const value of [null, 0, 9, 2.5]) fixture.componentInstance.onConcurrency(value);
    TestBed.inject(HttpTestingController).expectNone('/api/settings');
    expect(fixture.componentInstance.concurrencyError()).toContain('whole number from 1 to 8');
  });
});
