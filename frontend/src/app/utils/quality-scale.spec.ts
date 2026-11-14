import { qualityLabel, qualityTerm, rfEquivalent } from './quality-scale';

const vaapiAv1 = { qbetter: 'low' as const, qterm: 'QP', qbands: [80, 92, 104, 116] };
const videoToolbox = { qbetter: 'high' as const, qterm: 'CQ', qbands: [75, 60, 45, 30] };

describe('quality scale', () => {
  it('labels values on the encoder\'s own scale', () => {
    expect(qualityLabel(60, vaapiAv1)).toBe('near-lossless, large files');
    expect(qualityLabel(100, vaapiAv1)).toBe('balanced');
    expect(qualityLabel(160, vaapiAv1)).toBe('small files, visibly softer');
    expect(qualityLabel(60, videoToolbox)).toBe('high quality');
    expect(qualityLabel(10, videoToolbox)).toBe('small files, visibly softer');
  });

  it('falls back to the x265 RF scale before capabilities load', () => {
    expect(qualityTerm(undefined)).toBe('RF');
    expect(qualityLabel(24, undefined)).toBe('balanced');
    expect(rfEquivalent(24, undefined)).toBe(24);
  });

  it('maps any scale onto RF through its cutoffs', () => {
    expect(rfEquivalent(104, vaapiAv1)).toBe(26);
    expect(rfEquivalent(110, vaapiAv1)).toBeCloseTo(27.5, 5);
    expect(rfEquivalent(140, vaapiAv1)).toBe(35);
    expect(rfEquivalent(60, videoToolbox)).toBe(23);
    expect(rfEquivalent(30, videoToolbox)).toBe(29);
  });

  it('uses HandBrake\'s name for the scale', () => {
    expect(qualityTerm(vaapiAv1)).toBe('QP');
    expect(qualityTerm({ qbetter: 'high' })).toBe('CQ');
  });
});
