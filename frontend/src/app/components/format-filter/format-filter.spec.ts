import { ComponentFixture, TestBed } from '@angular/core/testing';
import { StoreService } from '../../services/store.service';
import { FormatFilterComponent } from './format-filter';

describe('FormatFilterComponent', () => {
  let fixture: ComponentFixture<FormatFilterComponent>;
  let store: StoreService;
  let changes: number;

  const options = (): HTMLButtonElement[] =>
    [...fixture.nativeElement.querySelectorAll('[aria-label="Filter by format"] button')];

  beforeEach(() => {
    fixture = TestBed.createComponent(FormatFilterComponent);
    store = TestBed.inject(StoreService);
    store.media.set('image');
    store.formats.set({ TIFF: 2, DNG: 1 });
    changes = 0;
    fixture.componentInstance.changed.subscribe(() => changes++);
    fixture.detectChanges();
  });

  it('lists the scanned formats with counts', () => {
    expect(options().map(b => b.textContent!.trim())).toEqual(['TIFF (2)', 'DNG (1)']);
  });

  it('toggles a format, goes back to page 1, drops hidden selections and asks for a reload', () => {
    store.page.set(3);
    store.selected.set(new Set(['a-dng-photo']));
    options()[0].click();
    fixture.detectChanges();
    expect(store.formatFilter()).toEqual(['TIFF']);
    expect(store.page()).toBe(1);
    expect(store.selected().size).toBe(0);
    expect(changes).toBe(1);
    expect(options()[0].getAttribute('aria-pressed')).toBe('true');
  });

  it('keeps a chosen format that a rescan no longer found', () => {
    store.formatFilter.set(['PNG']);
    fixture.detectChanges();
    expect(options().map(b => b.textContent!.trim())).toEqual(['TIFF (2)', 'DNG (1)', 'PNG (0)']);
  });

  it('shows nothing for videos or Live Photos', () => {
    for (const media of ['video', 'motionphoto'] as const) {
      store.media.set(media);
      store.jxlUnchecked.set(2);
      fixture.detectChanges();
      expect(options()).toEqual([]);
      expect(fixture.nativeElement.textContent).not.toContain('could not be checked');
    }
  });

  it('says how many JPEG XL files could not be checked', () => {
    store.jxlUnchecked.set(3);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('3 JPEG XL files could not be checked');
    store.jxlUnchecked.set(0);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).not.toContain('could not be checked');
  });
});
