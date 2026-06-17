import { ChangeDetectionStrategy, Component, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { StoreService } from '../../services/store.service';
import { ApiService } from '../../services/api.service';
import { MediaType } from '../../models/api.models';

@Component({
  selector: 'app-browse-dialog',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './browse-dialog.html',
  styleUrl: './browse-dialog.css',
})
export class BrowseDialogComponent {
  readonly store = inject(StoreService);
  private readonly api = inject(ApiService);
  readonly apply = output<void>();
  readonly cancel = output<void>();

  open = signal(false);
  // 'link' is a UI-only tab (add by link/ID), not a media type.
  localTab = signal<MediaType | 'link'>('video');
  localVideoGb = signal(0.5);
  localPhotoMb = signal(10);

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
    const sel = this.store.selectedKeys();
    const all = this.store.keyOwners().map(o => o.key_idx);
    this.localKeys.set(new Set(sel.length ? sel : all));  // empty stored = all
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

  hide(): void { this.open.set(false); }

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
    this.store.media.set(this.localTab() as MediaType);
    this.store.videoMinGb.set(this.localVideoGb());
    this.store.photoMinMb.set(this.localPhotoMb());
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
}
