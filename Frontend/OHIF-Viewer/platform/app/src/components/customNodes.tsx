import React from 'react';
import { Handle, Position } from '@xyflow/react'


export function StartNode({ data, isConnectable }) {
    return (
        <div
            style={{   // remove styling from here if possible
                background: '#4CAF50',
                color: 'white',
                padding: 10,
                borderRadius: '50%',
                width: 60,
                height: 60,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontWeight: 'bold',
            }}
        >
            {data.label || 'Start'}
            <Handle
                type="source"
                position={Position.Right}
                style={{ background: '#fff' }}
                isConnectable={isConnectable}
            />
        </div>
    )
}

export function AgentNode({ data, isConnectable }) {
    const { name, role } = data;

    return (
        <div
            style={{
                background: '#1E88E5',
                color: 'white',
                padding: 12,
                borderRadius: 10,
                width: 180,
                boxShadow: '0 2px 6px rgba(0,0,0,0.2)',
            }}
        >
            <Handle
                type="target"
                position={Position.Left}
                style={{ background: '#fff' }}
                isConnectable={isConnectable}
            />
            <div style={{ fontWeight: 'bold', marginBottom: 4 }}>{name}</div>
            <div style={{ fontSize: 12, opacity: 0.8 }}>{role}</div>
            <button
                onClick={() => alert(`Agent ${name} clicked!`)}
                style={{
                marginTop: 8,
                padding: '4px 8px',
                border: 'none',
                borderRadius: 4,
                background: 'white',
                color: '#1E88E5',
                fontWeight: 600,
                cursor: 'pointer',
                }}
            >
                Run Task
            </button>
            <Handle
                type="source"
                position={Position.Right}
                style={{ background: '#fff' }}
                isConnectable={isConnectable}
            />
        </div>
    );
}
