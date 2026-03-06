import React, { memo } from 'react';
import { StartNodeProps } from '../types';
import { FlaskConical } from 'lucide-react';

const START_W = 300;

const StartNode = memo((props: StartNodeProps) => {
  const { data } = props;

  return (
    <div
      style={{
        width: START_W,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        borderRadius: '12px',
        background: '#151a1f',
        border: '2px solid #3c5d80',
        color: '#91b9cd',
        padding: '10px 16px',
        boxSizing: 'border-box',
        textAlign: 'center',
        userSelect: 'none',
        position: 'relative',
        cursor: 'default',
        boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
        minHeight: 80,
      }}
    >
      {/* Header */}
      <div className="mb-1 flex items-center gap-2">
        <FlaskConical size={14} className="text-[#5acce6]" />
        <span className="text-xs font-bold uppercase tracking-wider text-[#5acce6]">
          {data.title || 'Start'}
        </span>
      </div>

      {/* Hypothesis / Description */}
      <div className="line-clamp-2 w-full text-[11px] leading-tight text-gray-300">
        {data.description || 'No hypothesis set.'}
      </div>

      {/* Output handle — pinned to START_HANDLE_Y */}
      <div
        style={{
          position: 'absolute',
          right: -5,
          top: '50%',
          transform: 'translateY(-50%)',
          width: 8,
          height: 8,
          background: '#566a84',
          border: '2px solid black',
          borderRadius: '50%',
        }}
      />
    </div>
  );
});

export default StartNode;