import React from 'react';
import { Pause, Play, RotateCcw, Wifi, WifiOff } from 'lucide-react';

interface CanvasHeaderProps {
  mode: 'planning' | 'executing' | 'results';
  canExecute: boolean;
  isPaused: boolean;
  isConnected: boolean;
  onStart: () => void;
  onTogglePause: () => void;
  onRestart: () => void;
  onReset: () => void;
}

export default function CanvasHeader({
  mode,
  canExecute,
  isPaused,
  isConnected,
  onStart,
  onTogglePause,
  onRestart,
  onReset,
}: CanvasHeaderProps) {
  return (
    <div className="absolute left-1/2 top-6 z-40 flex -translate-x-1/2 items-center gap-4 rounded-lg border border-[#3c5d80] bg-[#151a1f]/90 p-2 shadow-2xl backdrop-blur-md">
      <div className="flex items-center gap-3 border-r border-[#3c5d80] pl-2 pr-4">
        <h1 className="hidden text-[10px] font-bold uppercase tracking-wide text-[#91b9cd] xl:block">Workflow Engine</h1>
        <div
          className={`rounded border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
            mode === 'planning'
              ? 'border-[#3c5d80] bg-[#1f2d40] text-[#91b9cd]'
              : mode === 'executing'
                ? 'border-[#005f7d] bg-[#003f5c] text-[#5acce6]'
                : 'border-[#085a3c] bg-[#06422d] text-[#6fe5b8]'
          }`}
        >
          {mode}
        </div>
      </div>

      <div className="flex items-center gap-2">
        {mode === 'planning' && (
          <button
            onClick={onStart}
            disabled={!canExecute}
            className={`flex items-center gap-2 rounded px-4 py-1.5 text-xs font-medium text-white shadow-lg transition-colors ${
              canExecute ? 'cursor-pointer bg-[#348cfd] hover:bg-[#599fff]' : 'cursor-not-allowed bg-[#348cfd] opacity-50'
            }`}
          >
            <Play size={14} fill="currentColor" /> Execute
          </button>
        )}

        {mode === 'executing' && (
          <>
            <button
              onClick={onTogglePause}
              className={`flex items-center gap-2 rounded border px-3 py-1.5 text-xs font-medium transition-colors ${
                isPaused
                  ? 'border-[#705228] bg-[#3b2d1c] text-[#f8ce46]'
                  : 'border-[#3c5d80] bg-[#151a1f] text-[#91b9cd] hover:border-[#91b9cd]'
              }`}
            >
              {isPaused ? <Play size={14} /> : <Pause size={14} />}
              {isPaused ? 'Resume' : 'Pause'}
            </button>
            <button
              onClick={onRestart}
              className="flex items-center gap-2 rounded border border-[#3c5d80] bg-[#151a1f] px-3 py-1.5 text-xs text-[#91b9cd] transition-colors hover:bg-[#2c1a1f] hover:text-[#ff6b6b]"
            >
              <RotateCcw size={14} /> Restart
            </button>
          </>
        )}

        {mode === 'results' && (
          <button
            onClick={onReset}
            className="flex items-center gap-2 rounded bg-[#6fe5b8] px-4 py-1.5 text-xs font-medium text-black transition-colors hover:bg-[#91eac8]"
          >
            <RotateCcw size={14} /> Reset
          </button>
        )}
      </div>

      <div className="flex items-center border-l border-[#3c5d80] pl-2 text-[10px] text-[#566a84]" title={isConnected ? 'WebSocket Connected' : 'Using Mock Data'}>
        {isConnected ? <Wifi size={14} className="text-[#6fe5b8]" /> : <WifiOff size={14} />}
      </div>
    </div>
  );
}
