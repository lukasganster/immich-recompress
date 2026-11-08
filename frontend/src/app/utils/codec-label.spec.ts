import { codecLabel } from './codec-label';

describe('codecLabel', () => {
  it('uses the familiar names for common codecs', () => {
    expect(codecLabel('hevc')).toBe('HEVC');
    expect(codecLabel('H264')).toBe('H.264');
  });

  it('uppercases anything else', () => {
    expect(codecLabel('prores')).toBe('PRORES');
  });
});
