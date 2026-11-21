import { provideHttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { JobPublic } from '../../models/api.models';
import { StoreService } from '../../services/store.service';
import { ReviewModalComponent } from './review-modal';

describe('JPEG XL review', () => {
  it('labels the encoded format and lossless mode instead of a savings target', () => {
    TestBed.configureTestingModule({
      imports: [ReviewModalComponent], providers: [provideHttpClient()],
    });
    const fixture = TestBed.createComponent(ReviewModalComponent);
    const store = TestBed.inject(StoreService);
    store.jobs.set({ photo: {
      id: 'photo', name: 'photo.jpg', media: 'image', status: 'review', codec: 'JPEG',
      new_codec: 'JPEG XL', photo_format: 'jxl', old_size: 1000, new_size: 750,
    } as JobPublic });
    fixture.componentInstance.show('photo');
    fixture.detectChanges();
    const element: HTMLElement = fixture.nativeElement;
    expect(element.textContent).toContain('JPEG → JPEG XL · .jxl');
    expect(element.textContent).toContain('Lossless JPEG reconstruction');
    expect(element.textContent).not.toContain('Target savings');
    expect(element.querySelector('img[data-role="result"]')!.getAttribute('src')).toBe('/api/preview/photo');
  });

  it('does not promise JPEG reconstruction for a converted TIFF', () => {
    TestBed.configureTestingModule({
      imports: [ReviewModalComponent], providers: [provideHttpClient()],
    });
    const fixture = TestBed.createComponent(ReviewModalComponent);
    TestBed.inject(StoreService).jobs.set({ photo: {
      id: 'photo', name: 'scan.tif', media: 'image', status: 'review', codec: 'TIF',
      new_codec: 'JPEG XL', photo_format: 'jxl', old_size: 1000, new_size: 750,
    } as JobPublic });
    fixture.componentInstance.show('photo');
    fixture.detectChanges();
    const element: HTMLElement = fixture.nativeElement;
    expect(element.textContent).toContain('TIF → JPEG XL · .jxl');
    expect(element.textContent).toContain('Lossless JPEG XL');
    expect(element.textContent).not.toContain('Lossless JPEG reconstruction');
  });

  it('labels a JPEG the megapixel cap re-encoded from pixels as lossless JPEG XL', () => {
    TestBed.configureTestingModule({
      imports: [ReviewModalComponent], providers: [provideHttpClient()],
    });
    const fixture = TestBed.createComponent(ReviewModalComponent);
    TestBed.inject(StoreService).jobs.set({ photo: {
      id: 'photo', name: 'big.jpg', media: 'image', status: 'review', codec: 'JPG',
      new_codec: 'JPEG XL', photo_format: 'jxl', pixel_path: true, old_size: 1000, new_size: 750,
    } as JobPublic });
    fixture.componentInstance.show('photo');
    fixture.detectChanges();
    const element: HTMLElement = fixture.nativeElement;
    expect(element.textContent).toContain('Lossless JPEG XL');
    expect(element.textContent).not.toContain('Lossless JPEG reconstruction');
  });

  it('says what happens to the motion video of a live photo', () => {
    TestBed.configureTestingModule({
      imports: [ReviewModalComponent], providers: [provideHttpClient()],
    });
    const fixture = TestBed.createComponent(ReviewModalComponent);
    TestBed.inject(StoreService).jobs.set({ photo: {
      id: 'photo', name: 'MVIMG.jpg', media: 'image', status: 'review', codec: 'JPG',
      new_codec: 'JPEG XL', photo_format: 'jxl', old_size: 1000, new_size: 750,
      motion_note: 'Motion video removed (setting: Remove)',
    } as JobPublic });
    fixture.componentInstance.show('photo');
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent)
      .toContain('Motion video removed (setting: Remove)');
  });

  it('shows the encoded distance even when the current settings have changed', () => {
    TestBed.configureTestingModule({
      imports: [ReviewModalComponent], providers: [provideHttpClient()],
    });
    const fixture = TestBed.createComponent(ReviewModalComponent);
    const store = TestBed.inject(StoreService);
    store.settings.update(s => ({ ...s, photo_jxl_distance: 0 }));
    store.jobs.set({ photo: {
      id: 'photo', name: 'photo.jpg', media: 'image', status: 'review', codec: 'JPEG',
      new_codec: 'JPEG XL', photo_format: 'jxl', photo_jxl_distance: 1.5,
      old_size: 1000, new_size: 750,
    } as JobPublic });
    fixture.componentInstance.show('photo');
    fixture.detectChanges();
    const element: HTMLElement = fixture.nativeElement;
    expect(element.textContent).toContain('Lossy JPEG XL · distance 1.5');
    expect(element.textContent).not.toContain('Lossless JPEG reconstruction');
  });
});
