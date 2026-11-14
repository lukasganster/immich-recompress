import { EncoderInfo } from '../models/api.models';

/** x265 RF cutoffs: the scale the labels and savings anchors were written for. */
export const RF_BANDS: readonly number[] = [20, 23, 26, 29];

const BAND_LABELS = [
  'near-lossless, large files',
  'high quality',
  'balanced',
  'smaller, some quality loss',
  'small files, visibly softer',
];

type Scale = Partial<Pick<EncoderInfo, 'qbetter' | 'qterm' | 'qbands'>> | undefined;

/** HandBrake's name for the encoder's quality scale (RF, CQ, QP...). */
export function qualityTerm(enc: Scale): string {
  return enc?.qterm ?? (enc?.qbetter === 'high' ? 'CQ' : 'RF');
}

/** Label cutoffs, best quality first; the RF ones when the encoder has none. */
export function qualityBands(enc: Scale): readonly number[] {
  return enc?.qbands?.length === 4 ? enc.qbands : RF_BANDS;
}

/** Plain-language label for a quality value on the encoder's own scale. */
export function qualityLabel(q: number, enc: Scale): string {
  const high = enc?.qbetter === 'high';
  const i = qualityBands(enc).findIndex(cut => (high ? q >= cut : q <= cut));
  return BAND_LABELS[i === -1 ? 4 : i];
}

/** Map a value onto the x265 RF scale by matching band cutoffs, so the savings
 *  estimate works for any encoder. Extrapolates past the outer cutoffs. */
export function rfEquivalent(q: number, enc: Scale): number {
  const bands = qualityBands(enc);
  let i = 0;
  while (i < bands.length - 2 && (bands[1] > bands[0] ? q > bands[i + 1] : q < bands[i + 1])) i++;
  const [b0, b1] = [bands[i], bands[i + 1]];
  const [r0, r1] = [RF_BANDS[i], RF_BANDS[i + 1]];
  return r0 + (r1 - r0) * ((q - b0) / (b1 - b0));
}
