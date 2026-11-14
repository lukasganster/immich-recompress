import { ChangeDetectionStrategy, Component, HostListener, inject, signal } from '@angular/core';
import { CdkTrapFocus } from '@angular/cdk/a11y';
import { FormsModule } from '@angular/forms';
import { mdiClose } from '@mdi/js';
import { IconComponent } from '../icon/icon';
import { StoreService } from '../../services/store.service';
import { EncoderInfo, Settings } from '../../models/api.models';
import { codecLabel } from '../../utils/codec-label';
import { qualityBands, qualityLabel, qualityTerm } from '../../utils/quality-scale';

@Component({
  selector: 'app-settings-panel',
  standalone: true,
  imports: [FormsModule, CdkTrapFocus, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './settings-panel.html',
  styleUrl: './settings-panel.css',
})
export class SettingsPanelComponent {
  readonly store = inject(StoreService);

  readonly mdiClose = mdiClose;

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

  onJxlDistance(value: number | null): void {
    if (value !== null && Number.isFinite(value)) {
      this.update({ photo_jxl_distance: Math.min(25, Math.max(0, value)) });
    }
  }

  /** Switching encoders changes the quality scale, so reset quality to the new
   *  encoder's default rather than carry an out-of-range value across scales. */
  onEncoder(id: string): void {
    const e = this.store.encoders().find(x => x.id === id);
    this.update(e ? { encoder: id, quality: e.qdefault } : { encoder: id });
  }

  /** Effective CPU-core count (0/unset = all detected cores). */
  get cores(): number { return this.s.threads || this.store.cpuCount(); }

  /** Plain-language quality band on the selected encoder's own scale. */
  qualityLabel(q: number): string { return qualityLabel(q, this.enc); }

  /** HandBrake's name for the selected encoder's quality scale. */
  get qualityTerm(): string { return qualityTerm(this.enc); }

  /** Label cutoffs for the help text, best quality first. */
  get qualityBands(): readonly number[] { return qualityBands(this.enc); }

  // --- brief summary shown in the sidebar card ---
  get videoSummary(): string {
    const label = this.enc?.label ?? this.s.encoder;
    return `${label} · ${this.qualityTerm} ${this.s.quality}`;
  }

  get outputSummary(): string {
    const res = this.s.resolution === 'original' ? 'Original' : `${this.s.resolution}p`;
    const speed = this.enc && this.enc.hw ? 'hardware' : this.s.preset;
    const cores = this.enc?.cores ? ` · ${this.cores} cores` : '';
    return `${speed} · ${res}${cores}`;
  }

  get photoSummary(): string {
    if (this.s.photo_format === 'jxl') {
      return this.s.photo_jxl_distance > 0
        ? `JPEG XL · lossy · distance ${this.s.photo_jxl_distance}`
        : 'JPEG XL · lossless';
    }
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

  /** Codecs on the skip list, e.g. "HEVC, AV1"; empty when nothing is skipped. */
  get skipSummary(): string {
    return this.s.skip_codecs.split(',').map(c => c.trim()).filter(Boolean)
      .map(codecLabel).join(', ');
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
