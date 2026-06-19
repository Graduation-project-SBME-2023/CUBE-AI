export interface VolumeGif {
    seriesInstanceUid: string;
    gifUrl: string;
}

export interface SegmentationData {
    volumes?: VolumeGif[];
    targetRoi?: string;
    selectedModel?: string;
    diceScore?: number | null;
    sessionId?: string;
}

export interface ReviewContainerProps {
    segmentationData?: SegmentationData | null;
}

export type ReviewStatus = 'accepted' | 'rejected' | 'pending' | 'modified';
