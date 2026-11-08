import { statusLabel } from './job-status';

describe('statusLabel', () => {
  it('names the reason beside a skip', () => {
    expect(statusLabel('skipped', 'HEVC')).toBe('skipped · HEVC');
  });

  it('falls back to a plain skip when the reason is unknown', () => {
    expect(statusLabel('skipped', null)).toBe('skipped');
  });

  it('ignores a reason on any other state', () => {
    expect(statusLabel('done', 'HEVC')).toBe('done');
  });

  it('shows idle as a dash and a processed asset as done', () => {
    expect(statusLabel('idle')).toBe('–');
    expect(statusLabel('processed')).toBe('done');
  });
});
