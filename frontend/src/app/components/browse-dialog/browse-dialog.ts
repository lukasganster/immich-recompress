import { ChangeDetectionStrategy, Component, HostListener, OnDestroy, computed, effect, untracked, inject, output, signal } from '@angular/core';
import { CdkTrapFocus } from '@angular/cdk/a11y';
import { FormsModule } from '@angular/forms';
import { StoreService } from '../../services/store.service';
import { ApiService } from '../../services/api.service';
import { Subscription } from 'rxjs';
import { BrowseOption, MediaType, MetadataFilter, emptyMetadataFilter } from '../../models/api.models';

@Component({
  selector: 'app-browse-dialog',
  standalone: true,
  imports: [FormsModule, CdkTrapFocus],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './browse-dialog.html',
  styleUrl: './browse-dialog.css',
})
export class BrowseDialogComponent implements OnDestroy {
  readonly store = inject(StoreService);
  private readonly api = inject(ApiService);
  readonly apply = output<void>();
  readonly cancel = output<void>();

  open = signal(false);
  // 'link' is a UI-only tab (add by link/ID), not a media type.
  localTab = signal<MediaType | 'link'>('video');
  localVideoGb = signal(0.5);
  localPhotoMb = signal(10);
  localDateFrom = signal('');
  localDateTo = signal('');
  localTimezone = signal('UTC');
  dateError = signal('');
  localMetadata = signal<MetadataFilter>(emptyMetadataFilter());
  albumSearch = signal('');
  peopleSearch = signal('');
  albums = signal<BrowseOption[]>([]);
  people = signal<BrowseOption[]>([]);
  albumsBusy = signal(false);
  peopleBusy = signal(false);
  albumError = signal('');
  peopleError = signal('');
  readonly discoveryBusy = computed(() => this.albumsBusy() || this.peopleBusy());
  readonly discoveryError = computed(() => [this.albumError(), this.peopleError()].filter(Boolean).join(' '));
  private discoveryRequest?: Subscription;
  private awaitingLibraries = false;
  readonly filteredAlbums = computed(() => this.albums().filter(a =>
    a.name.toLocaleLowerCase().includes(this.albumSearch().trim().toLocaleLowerCase())));
  readonly matchingPeople = computed(() => this.people().filter(p =>
    p.name.toLocaleLowerCase().includes(this.peopleSearch().trim().toLocaleLowerCase())));
  readonly filteredPeople = computed(() => this.matchingPeople().slice(0, 50));
  readonly selectedAlbumAvailable = computed(() => this.albums().some(a => a.id === this.localMetadata().albumId));
  readonly unavailable = computed(() => {
    const m = this.localMetadata();
    return (!!m.albumId && !this.albumsBusy() && !this.albumError() && !this.selectedAlbumAvailable()) ||
      (!this.peopleBusy() && !this.peopleError() && m.personIds.some(id => !this.people().some(p => p.id === id)));
  });
  readonly discoveryBlocked = computed(() => {
    const m = this.localMetadata();
    return (!!m.albumId && (this.albumsBusy() || !!this.albumError() || !this.selectedAlbumAvailable())) ||
      (!!m.personIds.length && (this.peopleBusy() || !!this.peopleError() || m.personIds.some(id => !this.people().some(p => p.id === id))));
  });
  readonly hasMetadata = computed(() => {
    const m = this.localMetadata();
    return !!(m.albumId || m.personIds.length || m.city || m.state || m.country);
  });
  /** A closed section still says what it is filtering by. */
  readonly albumSummary = computed(() => {
    const id = this.localMetadata().albumId;
    return id ? this.albums().find(a => a.id === id)?.name ?? 'Saved album' : '';
  });
  readonly locationSummary = computed(() => {
    const m = this.localMetadata();
    return [m.city, m.state, m.country].filter(Boolean).join(', ');
  });
  readonly supportsMetadata = computed(() => {
    const match = this.store.immichVersion().match(/(\d+)\.(\d+)(?:\.(\d+))?/);
    return !!match && (+match[1] > 3 || (+match[1] === 3 && +match[2] >= 2));
  });

  constructor() {
    effect(() => {
      if (!this.open()) return;
      const available = this.store.keyOwners().map(o => o.key_idx);
      if (this.awaitingLibraries && available.length) {
        const saved = this.store.selectedKeys();
        this.localKeys.set(new Set(saved.length ? saved.filter(i => available.includes(i)) : available));
        this.awaitingLibraries = false;
      }
      this.localKeys();
      this.supportsMetadata();
      untracked(() => this.loadDiscovery());
    });
  }

  setMetadata<K extends keyof MetadataFilter>(field: K, value: MetadataFilter[K]): void {
    this.localMetadata.update(m => ({ ...m, [field]: value }));
  }

  togglePerson(id: string): void {
    this.localMetadata.update(m => ({ ...m, personIds: m.personIds.includes(id)
      ? m.personIds.filter(p => p !== id) : [...m.personIds, id] }));
  }

  optionLibraries(option: BrowseOption): string {
    return option.key_indices.map(idx => this.store.keyOwners().find(o => o.key_idx === idx)?.name || `Key ${idx + 1}`).join(', ');
  }

  loadDiscovery(): void {
    this.discoveryRequest?.unsubscribe();
    this.albums.set([]);
    this.people.set([]);
    this.albumError.set('');
    this.peopleError.set('');
    this.albumsBusy.set(false);
    this.peopleBusy.set(false);
    if (!this.supportsMetadata() || !this.localKeys().size) return;
    const keys = [...this.localKeys()].sort((a, b) => a - b);
    this.discoveryRequest = new Subscription();
    for (const kind of ['albums', 'people'] as const) {
      const items = kind === 'albums' ? this.albums : this.people;
      const busy = kind === 'albums' ? this.albumsBusy : this.peopleBusy;
      const error = kind === 'albums' ? this.albumError : this.peopleError;
      busy.set(true);
      this.discoveryRequest.add(this.api.browseOptions(kind, keys).subscribe({
        next: data => { items.set(data.items); busy.set(false); },
        error: response => {
          const detail = response.error?.error || 'Check the API keys and try again.';
          error.set(`Could not load ${kind}. ${detail}`);
          busy.set(false);
        },
      }));
    }
  }

  ngOnDestroy(): void { this.discoveryRequest?.unsubscribe(); }

  // --- add by link / ID ---
  addInput = signal('');
  addBusy = signal(false);
  addMsg = signal('');

  // --- per-key (per-user) library selection ---
  localKeys = signal<Set<number>>(new Set());

  show(): void {
    this.localTab.set(this.store.media());
    this.localVideoGb.set(this.store.videoMinGb());
    this.localPhotoMb.set(this.store.photoMinMb());
    this.localDateFrom.set(this.store.captureDateFrom());
    this.localDateTo.set(this.store.captureDateTo());
    this.localTimezone.set(this.store.captureTimezone());
    this.dateError.set('');
    this.localMetadata.set({ ...this.store.metadata(), personIds: [...this.store.metadata().personIds] });
    this.albumSearch.set('');
    this.peopleSearch.set('');
    const sel = this.store.selectedKeys();
    const all = this.store.keyOwners().map(o => o.key_idx);
    this.awaitingLibraries = !all.length;
    this.localKeys.set(new Set(sel.length ? sel.filter(i => all.includes(i)) : all));  // empty stored = all
    this.addInput.set('');
    this.addMsg.set('');
    this.open.set(true);
  }

  keyChecked(idx: number): boolean { return this.localKeys().has(idx); }

  toggleKey(idx: number): void {
    this.localKeys.update(s => {
      const n = new Set(s);
      n.has(idx) ? n.delete(idx) : n.add(idx);
      return n;
    });
  }

  hide(): void {
    this.discoveryRequest?.unsubscribe();
    this.albumsBusy.set(false);
    this.peopleBusy.set(false);
    this.open.set(false);
  }

  clearDates(): void {
    this.localDateFrom.set('');
    this.localDateTo.set('');
    this.dateError.set('');
  }

  /** Resolve the pasted Immich URLs / `/photos/<id>` / bare ids and add the
   *  matching assets to the list, pre-selected. Closes the dialog only when
   *  everything resolved, so unresolved inputs stay visible for fixing. */
  addByLink(): void {
    const items = this.addInput().split(/[\s,]+/).map(s => s.trim()).filter(Boolean);
    if (!items.length || this.addBusy()) return;
    this.addBusy.set(true);
    this.addMsg.set('Resolving…');
    this.api.resolve(items).subscribe({
      next: res => {
        this.addBusy.set(false);
        this.store.addResolved(res.assets);
        const ok = res.assets.length, bad = res.errors.length;
        if (ok && !bad) {
          this.addInput.set('');
          this.hide();
        } else if (ok) {
          this.addInput.set('');
          this.addMsg.set(`Added ${ok}; ${bad} not found.`);
        } else {
          this.addMsg.set(bad ? `None found (${bad} input${bad > 1 ? 's' : ''} failed).` : 'Nothing to add.');
        }
      },
      error: () => { this.addBusy.set(false); this.addMsg.set('Failed to resolve.'); },
    });
  }

  onApply(): void {
    if (this.localTab() === 'link') { this.addByLink(); return; }
    if (!this.localKeys().size || (this.hasMetadata() &&
        (!this.supportsMetadata() || this.discoveryBlocked()))) return;
    if (this.localDateFrom() && this.localDateTo()
        && this.localDateFrom() > this.localDateTo()) {
      this.dateError.set('The start date must not be after the end date.');
      return;
    }
    const metadata = this.localMetadata();
    this.store.metadata.set({ ...metadata, city: metadata.city.trim(),
      state: metadata.state.trim(), country: metadata.country.trim() });
    this.store.media.set(this.localTab() as MediaType);
    this.store.videoMinGb.set(this.localVideoGb());
    this.store.photoMinMb.set(this.localPhotoMb());
    this.store.captureDateFrom.set(this.localDateFrom());
    this.store.captureDateTo.set(this.localDateTo());
    this.store.captureTimezone.set(this.localTimezone());
    // Store all-selected (or none) as [] meaning "scan all keys".
    const owners = this.store.keyOwners();
    const chosen = owners.map(o => o.key_idx).filter(i => this.localKeys().has(i));
    this.store.selectedKeys.set(chosen.length === owners.length ? [] : chosen);
    this.store.saveBrowse();
    this.store.page.set(1);
    this.store.clearSelection();
    this.hide();
    this.apply.emit();
  }

  clearHistory(): void {
    const n = Object.keys(this.store.processed()).length;
    if (!n) return;
    if (!confirm(`Really clear the processing history (${n} files)?`)) return;
    this.store.clearProcessed();
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.open()) this.hide();
  }
}
