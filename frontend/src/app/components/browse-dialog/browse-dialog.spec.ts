import { TestBed } from '@angular/core/testing';
import { of, Subject, throwError } from 'rxjs';
import { vi } from 'vitest';
import { BrowseDialogComponent } from './browse-dialog';
import { ApiService } from '../../services/api.service';
import { StoreService } from '../../services/store.service';
import { BrowseOption, emptyMetadataFilter } from '../../models/api.models';

const album: BrowseOption = { id: 'album-a', name: 'Holiday', owner_id: 'user-a', key_indices: [0] };
const alex: BrowseOption = { id: 'person-a', name: 'Alex', owner_id: '', key_indices: [0] };

describe('BrowseDialogComponent', () => {
  let store: StoreService;
  let component: BrowseDialogComponent;
  let fixture: ReturnType<typeof TestBed.createComponent<BrowseDialogComponent>>;
  let options: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    const saved = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => saved.get(key) ?? null,
      setItem: (key: string, value: string) => saved.set(key, value),
      removeItem: (key: string) => saved.delete(key),
    });
    options = vi.fn((kind: string) => of({ items: kind === 'albums' ? [album] : [alex] }));
    TestBed.configureTestingModule({ providers: [{ provide: ApiService, useValue: { browseOptions: options } }] });
    store = TestBed.inject(StoreService);
    store.immichVersion.set('3.3.0');
    store.keyOwners.set([
      { key_idx: 0, id: 'user-a', name: 'Library A', email: '' },
      { key_idx: 1, id: 'user-b', name: 'Library B', email: '' },
    ]);
    fixture = TestBed.createComponent(BrowseDialogComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => vi.unstubAllGlobals());

  function show(): void { component.show(); fixture.detectChanges(); }

  it('keeps drafts separate until Apply and persists filter values', () => {
    show();
    component.setMetadata('albumId', album.id);
    component.togglePerson(alex.id);
    component.setMetadata('city', ' Vienna ');
    expect(store.metadata()).toEqual(emptyMetadataFilter());
    component.onApply();
    expect(store.metadata()).toEqual({ ...emptyMetadataFilter(), albumId: album.id, personIds: [alex.id], city: 'Vienna' });
    expect(component.open()).toBe(false);
    expect(JSON.parse(localStorage.getItem('immich_browse')!).metadata).toEqual(store.metadata());
    store.metadata.set(emptyMetadataFilter());
    store.loadFromStorage();
    expect(store.metadata().personIds).toEqual([alex.id]);
  });

  it('Cancel discards edits and search never silently drops a selected album', async () => {
    store.metadata.set({ ...emptyMetadataFilter(), albumId: album.id });
    show();
    component.albumSearch.set('no match');
    fixture.detectChanges();
    expect(component.filteredAlbums()).toEqual([]);
    expect(component.localMetadata().albumId).toBe(album.id);
    await fixture.whenStable();
    const selected = fixture.nativeElement.querySelector('select') as HTMLSelectElement;
    expect(selected.value).toBe(album.id);
    component.setMetadata('country', 'Austria');
    component.hide();
    show();
    expect(component.localMetadata().country).toBe('');
  });

  it('requires unavailable saved filters to be cleared explicitly', () => {
    store.metadata.set({ ...emptyMetadataFilter(), personIds: ['missing'] });
    show();
    expect(component.unavailable()).toBe(true);
    component.onApply();
    expect(component.open()).toBe(true);
    expect(component.localMetadata().personIds).toEqual(['missing']);
    component.setMetadata('personIds', []);
    component.onApply();
    expect(component.open()).toBe(false);
  });

  it('cancels stale discovery when libraries change', () => {
    const oldAlbums = new Subject<{ items: BrowseOption[] }>();
    const oldPeople = new Subject<{ items: BrowseOption[] }>();
    options.mockImplementationOnce(() => oldAlbums).mockImplementationOnce(() => oldPeople);
    show();
    expect(component.discoveryBusy()).toBe(true);
    component.toggleKey(0);
    fixture.detectChanges();
    oldAlbums.next({ items: [{ ...album, id: 'stale' }] });
    oldAlbums.complete();
    oldPeople.next({ items: [] });
    oldPeople.complete();
    expect(component.albums()[0].id).toBe(album.id);
    expect(options).toHaveBeenCalledWith('albums', [1]);
  });

  it('does not broaden no libraries into all libraries', () => {
    show();
    component.toggleKey(0);
    component.toggleKey(1);
    fixture.detectChanges();
    component.onApply();
    expect(component.open()).toBe(true);
    expect(component.localKeys().size).toBe(0);
  });

  it('does not broaden a removed saved library into all libraries', () => {
    store.keyOwners.set([{ key_idx: 0, id: 'user-a', name: 'Library A', email: '' }]);
    store.selectedKeys.set([99]);
    show();
    component.onApply();
    expect(component.localKeys().size).toBe(0);
    expect(component.open()).toBe(true);
    expect(store.selectedKeys()).toEqual([99]);
    expect(fixture.nativeElement.textContent).toContain('Library A');
    component.toggleKey(0);
    fixture.detectChanges();
    component.onApply();
    expect(component.open()).toBe(false);
  });

  it('initializes libraries that arrive after the dialog opens', () => {
    store.keyOwners.set([]);
    show();
    expect(component.localKeys().size).toBe(0);
    store.keyOwners.set([{ key_idx: 0, id: 'user-a', name: 'Library A', email: '' }]);
    fixture.detectChanges();
    expect([...component.localKeys()]).toEqual([0]);
    expect(options).toHaveBeenCalledWith('albums', [0]);
  });

  it('shows discovery errors and preserves requested filters', () => {
    store.metadata.set({ ...emptyMetadataFilter(), albumId: album.id });
    options.mockReturnValue(throwError(() => ({ error: { error: 'Missing album permission' } })));
    show();
    expect(fixture.nativeElement.textContent).toContain('Missing album permission');
    component.onApply();
    expect(component.open()).toBe(true);
    expect(component.localMetadata().albumId).toBe(album.id);
  });

  it('allows location-only filtering when album and people discovery is unavailable', () => {
    options.mockReturnValue(throwError(() => ({ error: { error: 'Missing people permission' } })));
    show();
    component.setMetadata('country', 'Austria');
    component.onApply();
    expect(component.open()).toBe(false);
    expect(store.metadata().country).toBe('Austria');
  });

  it('allows album-only filtering when people discovery fails', () => {
    options.mockImplementation(kind => kind === 'albums' ? of({ items: [album] })
      : throwError(() => ({ error: { error: 'Missing people permission' } })));
    show();
    expect(component.albums()).toEqual([album]);
    expect(component.peopleError()).toContain('Missing people permission');
    expect((fixture.nativeElement.querySelector('select') as HTMLSelectElement).disabled).toBe(false);
    component.setMetadata('albumId', album.id);
    component.onApply();
    expect(component.open()).toBe(false);
    expect(store.metadata().albumId).toBe(album.id);
  });

  it('allows people-only filtering when album discovery fails', () => {
    options.mockImplementation(kind => kind === 'people' ? of({ items: [alex] })
      : throwError(() => ({ error: { error: 'Missing album permission' } })));
    show();
    expect(component.people()).toEqual([alex]);
    expect(component.albumError()).toContain('Missing album permission');
    component.togglePerson(alex.id);
    component.onApply();
    expect(component.open()).toBe(false);
    expect(store.metadata().personIds).toEqual([alex.id]);
  });

  it('keeps a requested people filter blocked while its discovery is pending', () => {
    const pending = new Subject<{ items: BrowseOption[] }>();
    options.mockImplementation(kind => kind === 'albums' ? of({ items: [album] }) : pending);
    store.metadata.set({ ...emptyMetadataFilter(), albumId: album.id, personIds: [alex.id] });
    show();
    expect(component.unavailable()).toBe(false);
    component.onApply();
    expect(component.open()).toBe(true);
    pending.next({ items: [alex] });
    pending.complete();
    component.onApply();
    expect(component.open()).toBe(false);
  });

  it('restores old saved browse settings with no metadata constraints', () => {
    localStorage.setItem('immich_browse', JSON.stringify({ media: 'image', captureDateFrom: '2026-01-01' }));
    store.loadFromStorage();
    expect(store.media()).toBe('image');
    expect(store.captureDateFrom()).toBe('2026-01-01');
    expect(store.metadata()).toEqual(emptyMetadataFilter());
  });

  it('limits rendered people while preserving selection outside a search', () => {
    show();
    component.people.set(Array.from({ length: 75 }, (_, i) => ({ ...alex, id: `person-${i}`, name: `Person ${i}` })));
    component.togglePerson('person-74');
    expect(component.filteredPeople()).toHaveLength(50);
    component.peopleSearch.set('Person 74');
    expect(component.filteredPeople().map(p => p.id)).toEqual(['person-74']);
    expect(component.localMetadata().personIds).toEqual(['person-74']);
  });

  it('loads discovery when the server version arrives after opening', () => {
    store.immichVersion.set('');
    show();
    expect(options).not.toHaveBeenCalled();
    store.immichVersion.set('3.2.2');
    fixture.detectChanges();
    expect(options).toHaveBeenCalledTimes(2);
  });
});
