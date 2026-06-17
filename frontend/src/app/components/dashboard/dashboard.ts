import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { StoreService } from '../../services/store.service';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.css',
})
export class DashboardComponent {
  readonly store = inject(StoreService);

  humanMB(bytes: number): string {
    if (!bytes) return '0 MB';
    const mb = bytes / (1024 * 1024);
    if (mb >= 1024) return (mb / 1024).toFixed(2) + ' GB';
    return mb.toFixed(1) + ' MB';
  }

  humanTime(sec: number): string {
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return `~${sec} s`;
    if (sec < 3600) return `~${Math.round(sec / 60)} min`;
    const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
    return `~${h} h${m ? ' ' + m + ' min' : ''}`;
  }

  get noun(): string {
    const m = this.store.media();
    return m === 'image' ? 'Photos' : m === 'motionphoto' ? 'Live Photos' : 'Videos';
  }

  get hasSelection(): boolean { return this.store.selected().size > 0; }

  get potLabel(): string {
    const n = this.store.selected().size;
    return n > 0 ? `Estimated Savings (${n} selected)` : 'Potential Savings (estimated)';
  }

  get potValue(): string {
    if (this.hasSelection) {
      const bytes = this.store.estimatedSavingsBytes();
      const frac = this.store.estimatedSavingsFraction();
      const totalSel = this.store.selectionBytes();
      return `≈ ${this.humanMB(bytes)} (−${Math.round(frac * 100)}% of ${this.humanMB(totalSel)})`;
    }
    const pot = this.store.totalPotential();
    const tot = this.store.totalSize();
    const pct = tot ? Math.round(pot / tot * 100) : 0;
    return `≈ ${this.humanMB(pot)} (−${pct}%)`;
  }

  get potSub(): string {
    if (!this.hasSelection) return '';
    return `⏱ est. ${this.humanTime(this.store.estimatedSeconds())}`;
  }
}
