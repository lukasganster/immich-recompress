import { formatBitrate } from './format-bitrate';

describe('formatBitrate', () => {
  it('writes megabits with one decimal', () => {
    expect(formatBitrate(18_400_000)).toBe('18.4 Mbps');
  });

  it('writes lower rates in kilobits', () => {
    expect(formatBitrate(850_400)).toBe('850 kbps');
  });

  it('writes a dash when the rate is unknown', () => {
    expect(formatBitrate(null)).toBe('—');
    expect(formatBitrate(0)).toBe('—');
    expect(formatBitrate('abc')).toBe('—');
  });
});
