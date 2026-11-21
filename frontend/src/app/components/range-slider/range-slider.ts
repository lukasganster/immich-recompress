import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

/**
 * One track with a min and a max thumb: two native range inputs laid over
 * each other, so each thumb keeps its own name and keyboard support. A null
 * `high` means no upper bound and parks the max thumb at the right end, as
 * does a bound above `max` (typed into a number box).
 */
@Component({
  selector: 'app-range-slider',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './range-slider.html',
  styleUrl: './range-slider.css',
})
export class RangeSliderComponent {
  readonly min = input(0);
  readonly max = input(100);
  readonly step = input(1);
  readonly low = input.required<number>();
  readonly high = input.required<number | null>();
  readonly lowLabel = input('Minimum');
  readonly highLabel = input('Maximum');
  /** Text under the left end of the track, e.g. "0 MB" or "Any". */
  readonly minText = input('');

  readonly lowChange = output<number>();
  readonly highChange = output<number | null>();

  readonly highPos = computed(() => {
    const high = this.high();
    return high === null ? this.max() : Math.min(Math.max(high, this.min()), this.max());
  });
  readonly lowPos = computed(() => Math.min(Math.max(this.low(), this.min()), this.highPos()));
  readonly highText = computed(() => this.high() === null ? 'No limit' : String(this.high()));
  readonly fillLeft = computed(() => this.percent(this.lowPos()));
  readonly fillRight = computed(() => 100 - this.percent(this.highPos()));

  private percent(value: number): number {
    const span = this.max() - this.min();
    return span > 0 ? ((value - this.min()) / span) * 100 : 0;
  }

  onLow(event: Event): void {
    const thumb = event.target as HTMLInputElement;
    const value = Math.min(+thumb.value, this.highPos());
    thumb.value = String(value);  // the thumbs can't cross
    this.lowChange.emit(value);
  }

  onHigh(event: Event): void {
    const thumb = event.target as HTMLInputElement;
    const value = Math.max(+thumb.value, this.lowPos());
    thumb.value = String(value);
    this.highChange.emit(value >= this.max() ? null : value);
  }
}
