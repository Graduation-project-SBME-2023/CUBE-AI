import React, { useState } from "react";
import { Handle, Position } from '@xyflow/react'
import { X } from "lucide-react";

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
                cursor: "default",
            }}
        >
            {data.label || 'Start'}
            <Handle
                type="source"
                position={Position.Right}
                style={{background: "transparent" ,border: "none"}}
                isConnectable={isConnectable}
            />
        </div>
    )
}
export function AgentNode({ data, isConnectable, id }) {
  const { name, description, editable, onDelete} = data;
  const [hovered, setHovered] = useState(false);

  return (
    <div
      className="relative transition-all duration-300 rounded-xl shadow-md text-white flex flex-col items-center justify-center px-4"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: hovered ? "#2563eb" : "#3b82f6", 
        width: 180,
        minHeight: hovered ? 140 : 60,
        overflow: "hidden",
        textAlign: "center",
        cursor: "default",
      }}
    >
      {/* delete button appears in edit mode */}
      {editable && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete?.(id);
          }}
          className="absolute -top-0 -right-0 bg-red-500 hover:bg-red-600 text-white rounded-full p-1 shadow-md"
          title="Delete step"
        >
          <X size={12} />
        </button>
      )}

      <Handle
        type="target"
        position={Position.Left}
        style={{ background: "transparent", border: "none" }}
        isConnectable={isConnectable}
      />

      {!hovered ? (
        <div className="font-bold text-base">{name}</div>
      ) : (
        <div className="text-blue-100 bg-blue-800/30 mt-2 rounded-md text-sm p-2 leading-snug">
          {description}
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        style={{ background: "transparent", border: "none" }}
        isConnectable={isConnectable}
      />
    </div>
  );
}
