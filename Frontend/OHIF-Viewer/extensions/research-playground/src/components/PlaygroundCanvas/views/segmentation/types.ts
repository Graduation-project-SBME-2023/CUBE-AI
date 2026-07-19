export interface VolumeGif {
    seriesInstanceUid: string;
    gifUrl: string;
}

export interface SegmentationData {
    volumes?: VolumeGif[];
    targetRoi?: string;
    selectedModel?: string;
    sessionId?: string;
    isFinished?: boolean;
}

export interface ReviewContainerProps {
    segmentationData?: SegmentationData | null;
}

export type ReviewStatus = 'accepted' | 'rejected' | 'pending' | 'modified';
