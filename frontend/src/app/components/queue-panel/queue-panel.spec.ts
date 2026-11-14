import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { JobPublic } from '../../models/api.models';
import { StoreService } from '../../services/store.service';
import { QueuePanelComponent } from './queue-panel';

const reviewed = (id: string): JobPublic => ({
  id, name: `${id}.dng`, media: 'image', status: 'review', codec: 'dng',
  old_size: 100 * 1024 * 1024, new_size: 40 * 1024 * 1024,
} as JobPublic);

describe('replace all media', () => {
  let fixture: ComponentFixture<QueuePanelComponent>;
  let store: StoreService;
  let http: HttpTestingController;

  const element = (): HTMLElement => fixture.nativeElement;
  const button = (label: string) => [...element().querySelectorAll('button')]
    .find(b => b.textContent!.trim() === label);
  const show = async (...ids: string[]) => {
    store.jobs.set(Object.fromEntries(ids.map(id => [id, reviewed(id)])));
    await fixture.whenStable();
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [QueuePanelComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    fixture = TestBed.createComponent(QueuePanelComponent);
    store = TestBed.inject(StoreService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('stays out of the way for a single review', async () => {
    await show('one');
    expect(element().querySelector('.replace-all')).toBeNull();
  });

  it.each([0, 1.5])('labels JPEG XL reviews with distance %s as .jxl output', async distance => {
    store.jobs.set({ photo: {
      ...reviewed('photo'), name: 'photo.jpg', codec: 'JPEG', new_codec: 'JPEG XL',
      photo_format: 'jxl', photo_jxl_distance: distance,
    } });
    await fixture.whenStable();
    const review = element().querySelector('[aria-label="Awaiting review"]')!;
    expect(review.textContent).toContain('JPEG → JPEG XL · .jxl');
    expect(review.textContent).not.toContain('JPEG → JPEG · .jpg');
    expect(review.querySelector('img[alt="The recompressed image"]')!.getAttribute('src'))
      .toBe('/api/preview/photo');
  });

  it('counts the reviews and states what they reclaim together', async () => {
    await show('one', 'two', 'three');
    expect(button('Replace all media (3)')).toBeTruthy();
    expect(element().querySelector('.replace-all')!.textContent).toContain('180.0 MB');
  });

  it('never replaces on the first click', async () => {
    await show('one', 'two');
    button('Replace all media (2)')!.click();
    await fixture.whenStable();
    http.expectNone('/api/confirm-all');
    expect(element().querySelector('.replace-all')!.textContent).toContain('Replace 2 originals in Immich?');
  });

  it('sends the reviews on screen once confirmed, then refreshes', async () => {
    const refreshed = vi.fn();
    fixture.componentInstance.jobsRefresh.subscribe(refreshed);
    await show('one', 'two');
    button('Replace all media (2)')!.click();
    await fixture.whenStable();
    button('Replace 2')!.click();
    const request = http.expectOne('/api/confirm-all');
    expect(request.request.body).toEqual({ ids: ['one', 'two'] });
    request.flush({ confirmed: ['one', 'two'], skipped: [] });
    expect(refreshed).toHaveBeenCalled();
  });

  it('backs out without touching anything', async () => {
    await show('one', 'two');
    button('Replace all media (2)')!.click();
    await fixture.whenStable();
    button('Cancel')!.click();
    await fixture.whenStable();
    http.expectNone('/api/confirm-all');
    expect(button('Replace all media (2)')).toBeTruthy();
  });

  it('forgets the first click once the bar has gone', async () => {
    await show('one', 'two');
    button('Replace all media (2)')!.click();
    await fixture.whenStable();
    await show('one');
    await show('one', 'two');
    expect(button('Replace all media (2)')).toBeTruthy();
  });
});

describe('finished skips', () => {
  let fixture: ComponentFixture<QueuePanelComponent>;
  let store: StoreService;

  const skipped = (changes: Partial<JobPublic>): JobPublic => ({
    id: 'one', name: 'IMG_8138.MOV', media: 'video', status: 'skipped',
    log: 'Source is already HEVC, which is on the skip list (Skip Codecs in Video settings)',
    skip_reason: 'HEVC', old_size: 3_500_000_000, new_size: null, ...changes,
  } as JobPublic);
  const finished = (): HTMLElement => fixture.nativeElement.querySelector('[aria-label="Finished"]');
  const show = async (job: JobPublic) => {
    store.jobs.set({ [job.id]: job });
    await fixture.whenStable();
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [QueuePanelComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    fixture = TestBed.createComponent(QueuePanelComponent);
    store = TestBed.inject(StoreService);
  });

  it('names the reason and explains it on hover', async () => {
    await show(skipped({}));
    const badge = finished().querySelector<HTMLElement>('.badge')!;
    expect(badge.textContent!.trim()).toBe('skipped · HEVC');
    expect(badge.title).toContain('already HEVC');
  });

  it('shows a skip below the minimum as a skip, not as a saving', async () => {
    await show(skipped({ skip_reason: '<10% saved', log: 'Savings 6.0% < 10%', old_size: 100, new_size: 94 }));
    expect(finished().querySelector('.badge')!.textContent!.trim()).toBe('skipped · <10% saved');
    expect(finished().querySelector('li .text-success')).toBeNull();
  });
});
