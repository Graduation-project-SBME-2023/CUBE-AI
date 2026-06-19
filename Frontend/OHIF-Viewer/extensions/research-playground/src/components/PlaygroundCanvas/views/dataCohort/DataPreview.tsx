import React, { useRef, useState } from 'react';
import { Database } from 'lucide-react';
import { getBackendApiBaseUrl } from '../../../../utils/apiBase';

const PausableGif = ({ uid, isHovered }: { uid: string; isHovered: boolean }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const [failed, setFailed] = useState(false);

  const handleLoad = () => {
    if (imgRef.current && canvasRef.current) {
      const img = imgRef.current;
      const canvas = canvasRef.current;
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.drawImage(img, 0, 0);
      }
    }
  };

  if (failed) {
    return (
      <div className="relative flex h-full w-full items-center justify-center bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-[#1f2d40] via-[#0b1015] to-black">
        <Database size={24} className="text-[#3c5d80]" />
      </div>
    );
  }

  return (
    <div className="relative h-full w-full overflow-hidden bg-black">
      <canvas
        ref={canvasRef}
        className="pointer-events-none absolute inset-0 z-10 h-full w-full object-cover"
        style={{ opacity: isHovered ? 0 : 0.5, transition: 'opacity 0.3s ease-in-out' }}
      />
      <img
        ref={imgRef}
        src={`${getBackendApiBaseUrl()}/volumes/${uid}/preview.gif`}
        alt={`Volume ${uid}`}
        className="absolute inset-0 z-20 h-full w-full object-cover"
        style={{ opacity: isHovered ? 0.9 : 0, transition: 'opacity 0.3s ease-in-out' }}
        onLoad={handleLoad}
        onError={() => setFailed(true)}
      />
    </div>
  );
};

interface DataPreviewProps {
  uids: string[];
}

export default function DataPreview({ uids }: DataPreviewProps) {
  const [hoveredUid, setHoveredUid] = useState<string | null>(null);

  return (
    <div className="grid grid-cols-2 gap-3 overflow-y-auto pb-4 pr-2 md:grid-cols-3 lg:grid-cols-4 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#151a1f] [&::-webkit-scrollbar]:w-2 hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">
      {uids.length > 0
        ? uids.map(uid => (
            <div
              key={uid}
              className="relative block w-full overflow-hidden rounded border border-[#3c5d80]/50 bg-[#05080a] transition-all group hover:border-[#5acce6]"
              style={{ paddingTop: '100%' }}
              onMouseEnter={() => setHoveredUid(uid)}
              onMouseLeave={() => setHoveredUid(null)}
            >
              <div className="absolute inset-0 flex flex-col">
                <div className="relative min-h-0 w-full flex-1 overflow-hidden bg-black">
                  <PausableGif uid={uid} isHovered={hoveredUid === uid} />
                  <div className="pointer-events-none absolute right-2 top-2 z-30">
                    <div className="rounded border border-[#5ce6b0]/30 bg-[#152e2e]/90 px-1.5 py-0.5 text-[8px] font-bold uppercase text-[#5ce6b0] shadow-lg backdrop-blur">
                      READY
                    </div>
                  </div>
                </div>
                <div className="relative z-20 flex h-7 w-full shrink-0 items-center justify-center border-t border-[#1f2d40] bg-[#0b1015] px-2 transition-colors group-hover:bg-[#151a1f]">
                  <span className="w-full truncate text-center font-mono text-[10px] text-[#91b9cd]" title={uid}>
                    ...{uid.slice(-12)}
                  </span>
                </div>
              </div>
            </div>
          ))
        : Array.from({ length: 12 }).map((_, i) => (
            <div
              key={`skeleton-${i}`}
              className="relative block w-full animate-pulse overflow-hidden rounded border border-[#1f2d40] bg-[#0b1015]"
              style={{ paddingTop: '100%' }}
            >
              <div className="absolute inset-0 flex items-center justify-center">
                <span className="font-mono text-[10px] text-gray-700">Awaiting Data</span>
              </div>
            </div>
          ))}
    </div>
  );
}
