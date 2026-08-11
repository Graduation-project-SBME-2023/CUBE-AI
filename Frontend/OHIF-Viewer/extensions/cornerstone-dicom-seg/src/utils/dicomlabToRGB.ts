import dcmjs from 'dcmjs';

const legacySegmentColors = [
  [221, 84, 84],
  [77, 228, 121],
  [166, 70, 235],
  [189, 180, 116],
  [109, 182, 196],
  [204, 101, 157],
  [123, 211, 94],
  [93, 87, 218],
];

/**
 * Converts a CIELAB color to an RGB color using the dcmjs library.
 * @param cielab - The CIELAB color to convert.
 * @returns The RGB color as an array of three integers between 0 and 255.
 */
function dicomlabToRGB(cielab: number[]): number[] {
  const rgb = dcmjs.data.Colors.dicomlab2RGB(cielab).map(x => Math.round(x * 255));

  return rgb;
}

function getSegmentRGBColor(cielab: unknown, segmentIndex: number): number[] {
  const hasValidCIELab =
    Array.isArray(cielab) &&
    cielab.length >= 3 &&
    cielab.slice(0, 3).every(value => Number.isFinite(Number(value)));

  if (hasValidCIELab) {
    return dicomlabToRGB(cielab.slice(0, 3).map(Number));
  }

  return legacySegmentColors[(segmentIndex - 1) % legacySegmentColors.length];
}

export { dicomlabToRGB, getSegmentRGBColor };
