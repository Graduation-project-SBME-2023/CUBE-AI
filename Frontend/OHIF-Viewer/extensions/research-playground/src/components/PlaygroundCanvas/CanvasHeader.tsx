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

        {mode === 'results' && (
          <button
            onClick={onReset}
            className="flex items-center gap-2 rounded bg-[#5e98ac] px-3 py-1 text-xs font-medium text-black transition-colors hover:bg-[#72adc2]"
          >
            <RotateCcw size={12} /> Reset
          </button>
        )}
      </div>

      {/* ── Literature Text ── */}
      {literatureEnabled && (
        <>
          <span className="text-[#33485d] text-xs font-bold px-1">•</span>
          <span className="flex items-center gap-1 text-xs font-medium text-[#7ec8e3]">
            <BookOpen size={12} />
            Literature
          </span>
        </>
      )}

      {/* ── Labelling Mode Toggle ── */}
      <span className="text-[#33485d] text-xs font-bold px-1">•</span>
      <button
        onClick={onToggleLabellingMode}
        disabled={labellingModeLocked}
        title={labellingModeLocked ? 'Labelling mode locked' : 'Toggle labelling method'}
        className={`flex items-center rounded border border-[#3c5d80] text-[10px] uppercase transition-all ${
          labellingModeLocked ? 'cursor-not-allowed opacity-50' : 'cursor-pointer hover:border-[#7ec8e3]'
        }`}
      >
        <span
          className={`flex items-center gap-1 px-2 py-1 transition-colors ${
            labellingMode === 'manual' ? 'bg-[#3c5d80] text-white' : 'text-gray-400'
          }`}
        >
          <Upload size={10} /> Manual
        </span>
        <span
          className={`flex items-center gap-1 px-2 py-1 transition-colors ${
            labellingMode === 'automatic' ? 'bg-[#3c5d80] text-white' : 'text-gray-400'
          }`}
        >
          <Bot size={10} /> Auto
        </span>
      </button>
    </div>
  );
}
