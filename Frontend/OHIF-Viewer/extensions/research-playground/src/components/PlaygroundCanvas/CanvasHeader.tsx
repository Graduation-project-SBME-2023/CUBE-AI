import React from 'react';
import { Pause, Play, RotateCcw, BookOpen, Bot, Upload, Square } from 'lucide-react';

interface CanvasHeaderProps {
  mode: 'planning' | 'executing' | 'results';
  literatureEnabled: boolean;
  canExecute: boolean;
  isPaused: boolean;
  isStopping: boolean;
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
  const badgeLabel =
    mode === 'planning' ? 'Planning' : mode === 'executing' ? 'Executing' : 'Results';

  return (
    <div className="absolute left-1/2 top-2 z-40 flex -translate-x-1/2 items-center gap-4 rounded-lg border border-[#3c5d80] bg-[#151a1f]/90 p-2 shadow-xl backdrop-blur-md">
      {/* ── Stage badge (morphs with mode) ── */}
      <div className="flex items-center gap-3 border-r border-[#3c5d80] pl-2 pr-4">
        <div
          className={`rounded border px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider transition-all ${
            mode === 'planning'
              ? 'border-[#3c5d80] bg-[#1f2d40] text-[#91b9cd]'
              : mode === 'executing'
                ? 'border-[#005f7d] bg-[#003f5c] text-[#5acce6] shadow-[0_0_10px_rgba(90,204,230,0.2)]'
                : 'border-[#085a3c] bg-[#06422d] text-[#6fe5b8] shadow-[0_0_10px_rgba(111,229,184,0.2)]'
          }`}
        >
          {badgeLabel}
        </div>
      </div>

      <div className="flex items-center gap-2">
        {mode === 'planning' && (
          <button
            onClick={onStart}
            disabled={!canExecute || !isConnected}
            className={`flex items-center gap-2 rounded-md px-5 py-2 text-sm font-semibold transition ${
              !canExecute || !isConnected
                ? 'cursor-not-allowed bg-[#3c5d80] text-gray-400 opacity-50'
                : 'bg-[#5e98ac] text-black hover:bg-[#6aa7bc]'
            }`}
          >
            <Play
              size={16}
              fill="currentColor"
            />
            Execute
          </button>
        )}

        {mode === 'executing' && (
          <>
            <button
              onClick={onTogglePause}
              disabled={isStopping || hasStopped}
              className={`flex items-center gap-1 rounded border px-1.5 py-1 text-[11px] font-medium transition-colors ${
                isStopping || hasStopped
                  ? 'cursor-not-allowed border-[#3c5d80] bg-[#1d2a33] text-[#5c7a99] opacity-50'
                  : isPaused
                    ? 'border-[#ffc107] bg-[#332b00] text-[#ffc107] hover:bg-[#4d4000]'
                    : 'border-[#3c5d80] bg-[#151a1f] text-[#91b9cd] hover:border-[#91b9cd]'
              }`}
            >
              {isPaused ? <Play size={12} /> : <Pause size={12} />}
              {isPaused ? 'Resume' : 'Pause'}
            </button>
            <button
              onClick={onStop}
              disabled={isStopping || hasStopped}
              className={`flex items-center gap-2 rounded border px-3 py-1.5 text-xs font-medium transition-colors ${
                isStopping || hasStopped
                  ? 'animate-pulse cursor-not-allowed border-[#7a1e1e] bg-[#3b1010] text-[#ff6b6b] opacity-80'
                  : 'border-[#3c5d80] bg-[#151a1f] text-[#91b9cd] hover:border-[#ff6b6b] hover:bg-[#2c1a1f] hover:text-[#ff6b6b]'
              }`}
            >
              <Square
                size={12}
                fill="currentColor"
              />
              {hasStopped ? 'Stopped' : isStopping ? 'Stopping...' : 'Stop'}
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
      {/* ── Literature Review badge ── */}
      {literatureEnabled && (
        <div
          className="flex items-center gap-1 rounded border border-[#5acce6] bg-[#1d2a33] px-2 py-1 text-[10px] font-medium text-[#5acce6]"
          title="Literature Review Enabled"
        >
          <BookOpen size={15} />
          <span className="hidden sm:inline">Literature</span>
        </div>
      )}

      {/* ── Labelling Mode Toggle ── */}
      <button
        onClick={onToggleLabellingMode}
        disabled={labellingModeLocked}
        title={
          labellingModeLocked
            ? 'Labelling mode locked once agent starts'
            : 'Toggle cohort labelling method'
        }
        className={`flex items-center gap-0 overflow-hidden rounded border text-[10px] font-bold uppercase tracking-wider transition-all ${
          labellingModeLocked
            ? 'cursor-not-allowed border-[#3c5d80] opacity-50'
            : 'cursor-pointer border-[#3c5d80] hover:border-[#5acce6]'
        }`}
      >
        {/* Manual side */}
        <span
          className={`flex items-center gap-1 px-2 py-1 transition-colors ${
            labellingMode === 'manual' ? 'bg-[#3c5d80] text-white' : 'bg-[#0b1015] text-[#3c5d80]'
          }`}
        >
          <Upload size={11} />
          Manual
        </span>
        {/* Auto side */}
        <span
          className={`flex items-center gap-1 px-2 py-1 transition-colors ${
            labellingMode === 'automatic'
              ? 'bg-[#005f7d] text-[#5acce6]'
              : 'bg-[#0b1015] text-[#3c5d80]'
          }`}
        >
          <Bot size={11} />
          Auto
        </span>
      </button>
    </div>
  );
}
