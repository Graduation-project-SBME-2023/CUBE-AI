export interface VolumeGif {
    seriesInstanceUid: string;
    gifUrl: string;
    isEmptyMask?: boolean;
    warning?: string;
}

export interface FailedVolume {
    seriesInstanceUid: string;
    reason?: string;
}

export interface SegmentationProgress {
    total?: number;
    generated?: number;
    failed?: number;
}

export interface SegmentationData {
    volumes?: VolumeGif[];
    failedVolumes?: FailedVolume[];
    progress?: SegmentationProgress;
    targetRoi?: string;
    selectedModel?: string;
    sessionId?: string;
    isFinished?: boolean;
}

export interface ReviewContainerProps {
    segmentationData?: SegmentationData | null;
}

export type ReviewStatus = 'accepted' | 'rejected' | 'pending' | 'modified';
