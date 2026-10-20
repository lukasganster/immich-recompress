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
    expect(element.querySelector('img')!.getAttribute('src')).toBe('/api/preview/photo');
  });
});
