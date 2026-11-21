import { ComponentFixture, TestBed } from '@angular/core/testing';
import { RangeSliderComponent } from './range-slider';

describe('RangeSliderComponent', () => {
  let fixture: ComponentFixture<RangeSliderComponent>;
  let lows: number[];
  let highs: (number | null)[];

  const thumbs = (): HTMLInputElement[] => [...fixture.nativeElement.querySelectorAll('input[type="range"]')];
  const move = (thumb: HTMLInputElement, value: number) => {
    thumb.value = String(value);
    thumb.dispatchEvent(new Event('input'));
  };

  beforeEach(() => {
    fixture = TestBed.createComponent(RangeSliderComponent);
    fixture.componentRef.setInput('min', 0);
    fixture.componentRef.setInput('max', 100);
    fixture.componentRef.setInput('low', 10);
    fixture.componentRef.setInput('high', 40);
    fixture.componentRef.setInput('lowLabel', 'Minimum photo size in MB');
    fixture.componentRef.setInput('highLabel', 'Maximum photo size in MB');
    lows = [];
    highs = [];
    fixture.componentInstance.lowChange.subscribe(v => lows.push(v));
    fixture.componentInstance.highChange.subscribe(v => highs.push(v));
    fixture.detectChanges();
  });

  it('clamps a low thumb dragged past the high thumb', () => {
    move(thumbs()[0], 70);
    expect(lows).toEqual([40]);
    expect(thumbs()[0].value).toBe('40');
  });

  it('clamps a high thumb dragged below the low thumb', () => {
    move(thumbs()[1], 5);
    expect(highs).toEqual([10]);
  });

  it('emits null for a high thumb at the right end and says no limit', () => {
    move(thumbs()[1], 100);
    expect(highs).toEqual([null]);
    fixture.componentRef.setInput('high', null);
    fixture.detectChanges();
    expect(thumbs()[1].value).toBe('100');
    expect(thumbs()[1].getAttribute('aria-valuetext')).toBe('No limit');
    expect(fixture.nativeElement.textContent).toContain('No limit');
  });

  it('parks a high bound above the track at the right end', () => {
    fixture.componentRef.setInput('high', 200);
    fixture.detectChanges();
    expect(thumbs()[1].value).toBe('100');
    expect(thumbs()[1].getAttribute('aria-valuetext')).toBe('200');
  });

  it('gives each thumb its own accessible name', () => {
    expect(thumbs().map(t => t.getAttribute('aria-label')))
      .toEqual(['Minimum photo size in MB', 'Maximum photo size in MB']);
  });
});
