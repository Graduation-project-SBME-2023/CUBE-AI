import React, { useState } from "react";
import { X } from "lucide-react";

export function StartNode({ data, isConnectable }) {
    return (
        <div
            style={{
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
        </div>
    )
}

export function AgentNode({ data, isConnectable, id }) {
  const { name, description, editable, onDelete, status = 'pending', logs = [] } = data;
  const [hovered, setHovered] = useState(false);

  // Status-based colors
  let bgColor = "#3b82f6";
  let borderColor = "#3b82f6";
  let textColor = "text-white";
  let statusIndicator = "🔵";

  if (status === 'pending') {
    bgColor = "#94a3b8";
    borderColor = "#94a3b8";
    statusIndicator = "⚪";
  } else if (status === 'active') {
    bgColor = "#2563eb";
    borderColor = "#1d4ed8";
    statusIndicator = "🔵";
  } else if (status === 'completed') {
    bgColor = "#10b981";
    borderColor = "#059669";
    statusIndicator = "✅";
  }

  return (
    <div
      className={`relative transition-all duration-300 rounded-xl shadow-md text-white flex flex-col items-center justify-center px-4 ${status === 'active' ? 'animate-pulse' : ''}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: hovered ? bgColor : bgColor, 
        borderColor: borderColor,
        border: `2px solid ${borderColor}`,
        width: 180,
        minHeight: hovered ? 140 : 60,
        overflow: "hidden",
        textAlign: "center",
        cursor: "default",
        boxShadow: status === 'active' ? `0 0 15px rgba(37, 99, 235, 0.6)` : 'none',
      }}
    >
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

      <div className="text-xs font-bold mb-1">{statusIndicator}</div>

      {!hovered ? (
        <div className="font-bold text-base">{name}</div>
      ) : (
        <div>
          <div className="font-bold text-base mb-2">{name}</div>
          <div className="text-blue-100 bg-black/20 rounded-md text-xs p-2 leading-snug max-h-20 overflow-y-auto">
            {description}
          </div>
          {logs.length > 0 && (
            <div className="text-yellow-200 text-xs mt-2 font-mono max-h-16 overflow-y-auto">
              {logs.slice(-2).map((log, idx) => (
                <div key={idx}>&gt; {log}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
