import resolveInferenceSegmentLabels, {
  normalizeClinicalLabel,
  synchronizeInferenceSegmentLabels,
} from './resolveInferenceSegmentLabels';

describe('resolveInferenceSegmentLabels', () => {
  it('labels a binary VoxTell mask with its prompt instead of the first server label', () => {
    expect(
      resolveInferenceSegmentLabels('voxtell', 'kidney', ['spleen', 'kidney'])
    ).toEqual(['kidney']);
  });

  it('normalizes the prompt form used in saved series descriptions', () => {
    expect(
      resolveInferenceSegmentLabels('VoxTell', 'left_kidney  lesion', [
        'spleen',
      ])
    ).toEqual(['left kidney lesion']);
  });

  it('preserves the server label catalog for fixed multi-label models', () => {
    const serverLabels = ['spleen', 'right kidney', 'left kidney'];

    expect(
      resolveInferenceSegmentLabels('vista', 'interactive', serverLabels)
    ).toBe(serverLabels);
  });

  it('refuses to assign an unrelated server label when the VoxTell target is unavailable', () => {
    expect(() =>
      resolveInferenceSegmentLabels('voxtell', '', ['spleen'])
    ).toThrow('VoxTell target label is required');
  });
});

describe('normalizeClinicalLabel', () => {
  it('trims and collapses target-label whitespace', () => {
    expect(normalizeClinicalLabel('  enhancing__brain   tumor ')).toBe(
      'enhancing brain tumor'
    );
  });
});

describe('synchronizeInferenceSegmentLabels', () => {
  it('replaces stale labels when inference reuses an existing segmentation volume', () => {
    const segmentation = {
      segments: [undefined, { segmentIndex: 1, label: 'spleen' }],
    };
    const segmentationService = {
      getSegmentation: jest.fn(() => segmentation),
      setSegmentLabel: jest.fn((_segmentationId, segmentIndex, label) => {
        segmentation.segments[segmentIndex].label = label;
      }),
    };

    synchronizeInferenceSegmentLabels(segmentationService, 'volume-1', [
      'kidney',
    ]);

    expect(segmentationService.setSegmentLabel).toHaveBeenCalledWith(
      'volume-1',
      1,
      'kidney'
    );
    expect(segmentation.segments[1].label).toBe('kidney');
  });
});
