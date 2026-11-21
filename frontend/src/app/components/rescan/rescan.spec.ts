import { ComponentFixture, TestBed } from '@angular/core/testing';
import { StoreService } from '../../services/store.service';
import { RescanComponent } from './rescan';

describe('RescanComponent', () => {
  let fixture: ComponentFixture<RescanComponent>;
  let store: StoreService;

  beforeEach(() => {
    fixture = TestBed.createComponent(RescanComponent);
    store = TestBed.inject(StoreService);
  });

  it('says how long ago the library was scanned', () => {
    store.loaded.set(true);
    store.scannedAt.set(new Date(Date.now() - 12 * 60_000 - 5_000).toISOString());
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Scanned 12 min ago');
    store.scannedAt.set(new Date().toISOString());
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Scanned just now');
  });

  it('asks for a rescan', () => {
    store.loaded.set(true);
    let asked = 0;
    fixture.componentInstance.rescan.subscribe(() => asked++);
    fixture.detectChanges();
    (fixture.nativeElement.querySelector('button') as HTMLButtonElement).click();
    expect(asked).toBe(1);
  });

  it('stays hidden before the first load', () => {
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('button')).toBeNull();
  });
});
