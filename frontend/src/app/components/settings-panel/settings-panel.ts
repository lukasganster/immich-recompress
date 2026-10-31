import { ChangeDetectionStrategy, Component, HostListener, inject, signal } from '@angular/core';
import { CdkTrapFocus } from '@angular/cdk/a11y';
import { FormsModule } from '@angular/forms';
import { StoreService } from '../../services/store.service';
import { EncoderInfo, Settings } from '../../models/api.models';

@Component({
  selector: 'app-settings-panel',
  standalone: true,
  imports: [FormsModule, CdkTrapFocus],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './settings-panel.html',
  styleUrl: './settings-panel.css',
})
export class SettingsPanelComponent {
  readonly store = inject(StoreService);

  /** Whether the edit dialog is open. */
  readonly open = signal(false);

  /** Photo section starts open when its alert would otherwise stay hidden.
   *  Read once per opening, so choosing JPEG does not collapse the section. */
  readonly expandPhoto = signal(false);

  show(): void {
    this.expandPhoto.set(this.jxlBlocked);
    this.open.set(true);
  }

  get s(): Settings { return this.store.settings(); }

  /** Spec of the currently-selected encoder (undefined until caps load). */
  get enc(): EncoderInfo | undefined { return this.store.selectedEncoder(); }

  update(patch: Partial<Settings>): void {
    this.store.settings.update(s => ({ ...s, ...patch }));
    this.store.saveSettings();
  }

  /** Switching encoders changes the quality scale, so reset quality to the new
   *  encoder's default rather than carry an out-of-range value across scales. */
  onEncoder(id: string): void {
    const e = this.store.encoders().find(x => x.id === id);
    this.update(e ? { encoder: id, quality: e.qdefault } : { encoder: id });
  }

  /** Effective CPU-core count (0/unset = all detected cores). */
  get cores(): number { return this.s.threads || this.store.cpuCount(); }

  /** Plain-language quality band, honouring the encoder's direction (RF-style
   *  lower-is-better vs VideoToolbox 0–100 higher-is-better). */
  qualityLabel(q: number): string {
    if (this.enc?.qbetter === 'high') {
      if (q >= 75) return 'near-lossless, large files';
      if (q >= 60) return 'high quality';
      if (q >= 45) return 'balanced';
      if (q >= 30) return 'smaller, some quality loss';
      return 'small files, visibly softer';
    }
    if (q <= 20) return 'near-lossless, large files';
    if (q <= 23) return 'high quality';
    if (q <= 26) return 'balanced';
    if (q <= 29) return 'smaller, some quality loss';
    return 'small files, visibly softer';
  }

  // --- brief summary shown in the sidebar card ---
  get videoSummary(): string {
    const label = this.enc?.label ?? this.s.encoder;
    const term = this.enc?.qbetter === 'high' ? 'CQ' : 'RF';
    return `${label} · ${term} ${this.s.quality}`;
  }

  get outputSummary(): string {
    const res = this.s.resolution === 'original' ? 'Original' : `${this.s.resolution}p`;
    const speed = this.enc && this.enc.hw ? 'hardware' : this.s.preset;
    const cores = this.enc?.cores ? ` · ${this.cores} cores` : '';
    return `${speed} · ${res}${cores}`;
  }

  get photoSummary(): string {
    if (this.s.photo_format === 'jxl') return 'JPEG XL · lossless';
    return `JPEG · −${this.s.photo_target_savings}% target${this.s.compress_raw ? ' · RAW→JPEG' : ''}`;
  }

  /** The saved choice is JPEG XL, but this server cannot produce it. */
  get jxlBlocked(): boolean {
    return this.s.photo_format === 'jxl' && !this.store.jpegXlAvailable();
  }

  get liveSummary(): string {
    const action = this.s.motion_action;
    return `${action.charAt(0).toUpperCase()}${action.slice(1)} motion video`;
  }

  get generalSummary(): string {
    return `${this.modeSummary} · min. ${this.s.min_savings}% savings`;
  }

  get modeSummary(): string {
    if (!this.s.replace) return 'Keep original';
    return this.s.confirm ? 'Replace after review' : 'Replace immediately';
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.open()) this.open.set(false);
  }
}
