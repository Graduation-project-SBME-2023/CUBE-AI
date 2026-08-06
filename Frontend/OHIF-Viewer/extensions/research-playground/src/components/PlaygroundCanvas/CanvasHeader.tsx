import React from 'react';
import { Pause, Play, RotateCcw, BookOpen, Bot, Upload, Square } from 'lucide-react';

interface CanvasHeaderProps {
  mode: 'planning' | 'generated' | 'manual' | 'executing' | 'results';
  literatureEnabled: boolean;
  canExecute: boolean;
  isPaused: boolean;
  isStopping: boolean;
  isResuming: boolean;
  hasStopped: boolean;
  isConnected: boolean;
  labellingMode: 'manual' | 'automatic';
  labellingModeLocked: boolean;
  onStart: () => void;
  onTogglePause: () => void;
  onStop: () => void;
  onReset: () => void;
  onToggleLabellingMode: () => void;
}

export default function CanvasHeader({
  mode,
  literatureEnabled,
  canExecute,
  isPaused,
  isStopping,
  isResuming,
  hasStopped,
  isConnected,
  labellingMode,
  labellingModeLocked,
  onStart,
  onTogglePause,
  onStop,
  onReset,
  onToggleLabellingMode,
}: CanvasHeaderProps) {
  // Single morphing badge label
  const isPlanningPhase = mode === 'planning' || mode === 'generated' || mode === 'manual';
  const badgeLabel =
    isPlanningPhase ? 'Planning' : mode === 'executing' ? 'Executing' : 'Results';

  return (
    <div className="absolute top-5 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 rounded-md border border-[#33485d] bg-[#151a1f]/95 px-4 py-2 shadow-lg backdrop-blur-md">
      {/* ── Stage Text ── */}
      <span className="text-xs font-semibold text-gray-200 uppercase tracking-wider">
        {badgeLabel}
      </span>
      
      {/* Separator */}
      <span className="text-[#33485d] text-xs font-bold px-1">•</span>

      {/* ── Main Actions ── */}
      <div className="flex items-center gap-2">
        {isPlanningPhase && (
          <button
            onClick={onStart}
            disabled={!canExecute || !isConnected}
            className={`flex items-center gap-2 rounded px-3 py-1 text-xs transition ${
              !canExecute || !isConnected
                ? 'cursor-not-allowed border border-[#33485d] text-gray-500 opacity-50'
                : 'bg-[#5e98ac] font-medium text-black hover:bg-[#72adc2]'
            }`}
          >
            <Play size={10} fill="currentColor" />
            Excute
          </button>
        )}

        {mode === 'executing' && (
          <>
            <button
              onClick={onTogglePause}
              disabled={isStopping || isResuming || hasStopped}
              className={`flex items-center gap-1 rounded border px-3 py-1 text-xs transition-colors ${
                isStopping || isResuming || hasStopped
                  ? 'cursor-not-allowed border-[#33485d] text-gray-500 opacity-50'
                  : isPaused
                    ? 'border-[#ffc107] text-[#ffc107] hover:bg-[#332b00]'
                    : 'border-[#3c5d80] text-gray-300 hover:bg-[#243242] hover:text-white'
              }`}
            >
              {isPaused ? <Play size={12} /> : <Pause size={12} />}
              {isResuming ? 'Resuming...' : isPaused ? 'Resume' : 'Pause'}
            </button>
            <button
              onClick={onStop}
              disabled={isStopping || hasStopped}
              className={`flex items-center gap-2 rounded border px-3 py-1 text-xs transition-colors ${
                isStopping || hasStopped
                  ? 'cursor-not-allowed border-[#7a1e1e] text-[#ff6b6b] opacity-80'
                  : 'border-[#3c5d80] text-gray-300 hover:border-[#ff6b6b] hover:text-[#ff6b6b]'
              }`}
            >
              <Square size={10} fill="currentColor" />
              {hasStopped ? 'Stopped' : isStopping ? 'Stopping...' : 'Stop'}
            </button>
          </>
        )}

      </div>


      {/* ── Labelling Mode Toggle ── */}
      <span className="text-[#33485d] text-xs font-bold px-1">•</span>
      <div className="flex items-center gap-2">
        <button
          onClick={onToggleLabellingMode}
          disabled={labellingModeLocked}
          title={labellingModeLocked ? 'Labeling mode is locked during execution' : 'Switch labeling mode'}
          className={`relative flex items-center rounded-full p-[2px] bg-[#0b1218] border border-[#1f2d40] shadow-inner transition-all ${
            labellingModeLocked ? 'cursor-not-allowed opacity-50' : 'cursor-pointer hover:border-[#3c5d80]'
          }`}
        >
          {/* Active Slider Background */}
          <div 
             className={`absolute inset-y-[2px] w-[calc(50%-2px)] rounded-full bg-[#5e98ac] transition-all duration-300 ease-out shadow-sm ${
               labellingMode === 'manual' ? 'left-[2px]' : 'left-[calc(50%)]'
             }`}
          />
          
          <span
            className={`relative z-10 flex items-center justify-center gap-1 px-2 py-[3px] text-[9px] uppercase tracking-wider font-bold transition-colors duration-300 w-24 whitespace-nowrap ${
              labellingMode === 'manual' ? 'text-black' : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            <Upload size={10} /> Manual
          </span>
          <span
            className={`relative z-10 flex items-center justify-center gap-1 px-2 py-[3px] text-[9px] uppercase tracking-wider font-bold transition-colors duration-300 w-24 whitespace-nowrap ${
              labellingMode === 'automatic' ? 'text-black' : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            <Bot size={10} /> Auto
          </span>
        </button>
      </div>
    </div>
  );
}
