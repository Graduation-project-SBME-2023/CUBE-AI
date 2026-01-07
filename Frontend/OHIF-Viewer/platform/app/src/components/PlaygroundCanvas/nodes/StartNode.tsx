import React, { memo } from 'react';
import { StartNodeProps } from '../types';
import { FlaskConical } from 'lucide-react';

const StartNode = memo((props: StartNodeProps) => {
  const { data } = props;
  
  // Make it wider to fit text
  const width = 300; 
  const height = 80;

  return (
    <div
      style={{
        width,
        height,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        borderRadius: '12px', // Softer corners
        background: '#151a1f',
        border: '2px solid #3c5d80',
        color: '#91b9cd',
        padding: '10px 16px',
        boxSizing: 'border-box',
        textAlign: 'center',
        userSelect: 'none',
        position: 'relative',
        cursor: 'default',
        boxShadow: '0 4px 12px rgba(0,0,0,0.3)'
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-1">
        <FlaskConical size={14} className="text-[#5acce6]" />
        <span className="font-bold text-xs uppercase tracking-wider text-[#5acce6]">
            {data.title || 'Start'}
        </span>
      </div>

      {/* Hypothesis / Description */}
      <div className="text-[11px] text-gray-300 leading-tight line-clamp-2 w-full">
        {data.description || "No hypothesis set."}
      </div>
      
      {/* Output handle indicator (Bottom Center) */}
      <div style={{
          position: 'absolute',
          bottom: -5,
          left: '50%',
          transform: 'translateX(-50%)',
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