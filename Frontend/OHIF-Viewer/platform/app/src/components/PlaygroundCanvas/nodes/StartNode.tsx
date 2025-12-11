import React, { memo } from 'react';
import { StartNodeProps } from '../types';

const StartNode = memo((props: StartNodeProps) => {
  const { data, nodeWidth = 100, nodeHeight = 50 } = props;
  return (
    <div
      style={{
        width: nodeWidth,
        height: nodeHeight,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        borderRadius: nodeHeight / 2,
        background: '#151a1f',
        border: '2px solid #3c5d80',
        color: '#91b9cd',
        fontWeight: 'bold',
        fontSize: 12,
        textTransform: 'uppercase',
        padding: '0 10px',
        boxSizing: 'border-box',
        textAlign: 'center',
        userSelect: 'none',
        position: 'relative',
        cursor: 'default',
      }}
    >
      {data.title || 'Start'}
      
      {/* Output handle indicator */}
      <div style={{
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