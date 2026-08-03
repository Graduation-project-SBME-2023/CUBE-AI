export interface VolumeGif {
    seriesInstanceUid: string;
    pairId?: string;
    targetId?: string;
    targetLabel?: string;
    gifUrl: string;
    isEmptyMask?: boolean;
    warning?: string;
    reviewStatus?: ReviewStatus;
}

export interface FailedVolume {
    seriesInstanceUid: string;
    pairId?: string;
    targetId?: string;
    reason?: string;
}

export interface SegmentationProgress {
    total?: number;
    generated?: number;
    failed?: number;
}

export interface SegmentationData {
    runId?: string;
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
