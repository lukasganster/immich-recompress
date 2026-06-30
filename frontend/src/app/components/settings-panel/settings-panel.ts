import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { StoreService } from '../../services/store.service';
import { EncoderInfo, Settings } from '../../models/api.models';

@Component({
  selector: 'app-settings-panel',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './settings-panel.html',
  styleUrl: './settings-panel.css',
})
export class SettingsPanelComponent {
  readonly store = inject(StoreService);

  /** Whether the edit dialog is open. */
  readonly open = signal(false);

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
    return `−${this.s.photo_target_savings}% target${this.s.compress_raw ? ' · RAW→JPEG' : ''}`;
  }

  get modeSummary(): string {
    if (!this.s.replace) return 'Keep original';
    return this.s.confirm ? 'Replace after review' : 'Replace immediately';
  }
}
