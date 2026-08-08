import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Loader2, X } from 'lucide-react';

export const SEGMENTATION_SUBMITTED_MESSAGE = 'cube:segmentation-submitted';
export const SEGMENTATION_CANCEL_MESSAGE = 'cube:segmentation-cancel';

export interface SegmentationSubmitPayload {
  sessionId: string;
  seriesInstanceUid: string;
  pairId: string;
  targetId?: string | null;
  status: 'saved';
  mask: {
    byteLength: number;
    dimensions?: number[];
  };
  overlay?: {
    gifUrl?: string;
    isEmptyMask?: boolean;
    updatedAt?: number;
  } | null;
}

interface EmbeddedSegmentationModalProps {
  viewerUrl: string;
  scanLabel: string;
  onCancel: () => void;
  onSubmitted: (payload: SegmentationSubmitPayload) => void;
}

const CLOSE_TRANSITION_MS = 140;

const isViewerWindow = (source: MessageEventSource | null, viewerWindow: Window | null) => {
  if (!source || !viewerWindow) {
    return false;
  }

  let candidate = source as Window;
  try {
    while (candidate) {
      if (candidate === viewerWindow) {
        return true;
      }
      if (candidate === candidate.parent) {
        return false;
      }
      candidate = candidate.parent;
    }
  } catch (_) {
    // Cross-origin descendants are never valid senders for this editor.
  }
  return false;
};

export default function EmbeddedSegmentationModal({
  viewerUrl,
  scanLabel,
  onCancel,
  onSubmitted,
}: EmbeddedSegmentationModalProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const closeTimerRef = useRef<number>();
  const [isVisible, setIsVisible] = useState(false);
  const [isViewerLoading, setIsViewerLoading] = useState(true);

  const closeWithTransition = (callback: () => void) => {
    setIsVisible(false);
    window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current = window.setTimeout(callback, CLOSE_TRANSITION_MS);
  };

  useEffect(() => {
    const animationFrame = window.requestAnimationFrame(() => setIsVisible(true));
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeWithTransition(onCancel);
      }
    };

    const handleMessage = (event: MessageEvent) => {
      if (
        event.origin !== window.location.origin ||
        !isViewerWindow(event.source, iframeRef.current?.contentWindow || null)
      ) {
        return;
      }

      if (event.data?.type === SEGMENTATION_CANCEL_MESSAGE) {
        closeWithTransition(onCancel);
        return;
      }

      if (event.data?.type === SEGMENTATION_SUBMITTED_MESSAGE && event.data.payload) {
        setIsVisible(false);
        onSubmitted(event.data.payload);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('message', handleMessage);

    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.clearTimeout(closeTimerRef.current);
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('message', handleMessage);
    };
  }, [onCancel, onSubmitted]);

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Interactive segmentation editor for ${scanLabel}`}
      className={`fixed inset-0 z-[10000] flex h-screen max-h-screen flex-col overflow-hidden bg-[#05080d] transition-opacity duration-150 ${
        isVisible ? 'opacity-100' : 'opacity-0'
      }`}
      style={{ height: '100dvh', maxHeight: '100dvh' }}
    >
      <div className="flex h-12 shrink-0 items-center border-b border-[#1f2d40] bg-[#0b1015] px-4 shadow-lg">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-white">Interactive Segmentation</p>
          <p className="truncate font-mono text-[10px] text-[#8aa5c0]">{scanLabel}</p>
        </div>
        <button
          type="button"
          onClick={() => closeWithTransition(onCancel)}
          className="ml-auto flex items-center gap-2 rounded-md border border-[#33445b] bg-[#121923] px-3 py-1.5 text-xs font-semibold text-[#d5deea] transition-colors hover:bg-[#1b2735]"
          aria-label="Close segmentation editor without saving"
        >
          <X size={15} />
          Cancel
        </button>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden bg-black">
        {isViewerLoading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#05080d]">
            <div className="flex items-center gap-3 rounded-lg border border-[#1f2d40] bg-[#0b1015] px-5 py-3 text-sm text-[#8aa5c0]">
              <Loader2
                size={18}
                className="animate-spin text-[#5acce6]"
              />
              Loading interactive viewer…
            </div>
          </div>
        )}
        <iframe
          ref={iframeRef}
          src={viewerUrl}
          title={`Interactive segmentation editor for ${scanLabel}`}
          className="block h-full max-h-full min-h-0 w-full border-0 bg-black"
          allow="fullscreen"
          allowFullScreen
          onLoad={() => setIsViewerLoading(false)}
        />
      </div>
    </div>,
    document.body
  );
}
