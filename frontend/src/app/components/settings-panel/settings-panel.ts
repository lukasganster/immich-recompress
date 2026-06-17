import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { StoreService } from '../../services/store.service';
import { Settings } from '../../models/api.models';

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

  private readonly ENC: Record<string, string> = {
    x265: 'HEVC (x265)', nvenc_h265: 'HEVC (NVENC)',
    qsv_h265: 'HEVC (QSV)', vce_h265: 'HEVC (VCE)',
  };

  get s(): Settings { return this.store.settings(); }

  update(patch: Partial<Settings>): void {
    this.store.settings.update(s => ({ ...s, ...patch }));
    this.store.saveSettings();
  }

  /** Plain-language band for an RF (rate-factor) value. */
  rfLabel(rf: number): string {
    if (rf <= 20) return 'near-lossless, large files';
    if (rf <= 23) return 'high quality';
    if (rf <= 26) return 'balanced';
    if (rf <= 29) return 'smaller, some quality loss';
    return 'small files, visibly softer';
  }

  // --- brief summary shown in the sidebar card ---
  get videoSummary(): string {
    return `${this.ENC[this.s.encoder] ?? this.s.encoder} · RF ${this.s.quality}`;
  }

  get outputSummary(): string {
    const res = this.s.resolution === 'original' ? 'Original' : `${this.s.resolution}p`;
    return `${this.s.preset} · ${res}`;
  }

  get photoSummary(): string {
    return `−${this.s.photo_target_savings}% target${this.s.compress_raw ? ' · RAW→JPEG' : ''}`;
  }

  get modeSummary(): string {
    if (!this.s.replace) return 'Keep original';
    return this.s.confirm ? 'Replace after review' : 'Replace immediately';
  }
}
